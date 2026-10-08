import asyncio
import itertools
import logging
from pathlib import Path

import pytest

from app.ingestion.chunkers import chunk_transcript
from app.ingestion.embedder import HashingEmbedder, OpenAIEmbedder
from app.ingestion.extractors import (
    extract,
    extract_pdf,
    extract_pptx,
    parse_caption_file,
)
from app.ingestion.jobs import run_ingestion
from app.ingestion.models import Segment
from app.ingestion.transcribe import (
    MAX_DURATION_OVERSHOOT_RATIO,
    clamp_segments_to_duration,
    enforce_duration_alignment,
    probe_media_duration_s,
    validate_transcript_payload,
)

VTT = """WEBVTT

00:00:00.000 --> 00:00:04.000
Welcome to the course. Today we cover vectors.

00:00:04.000 --> 00:00:09.500
A vector is an array of numbers. It represents meaning.

00:00:09.500 --> 00:00:15.000
Embeddings map text into this space. Similar text is nearby.

00:00:35.000 --> 00:00:40.000
Let us look at an example now.

00:00:40.000 --> 00:00:46.000
We embed the query and compare with stored vectors.

00:00:46.000 --> 00:00:52.000
Cosine similarity measures the angle between vectors.

01:10:00.000 --> 01:10:05.000
Now switching topics to databases.

01:10:05.000 --> 01:10:11.000
We store vectors in Postgres with pgvector.
"""


@pytest.fixture
def vtt_file(tmp_path: Path) -> Path:
    p = tmp_path / "lecture.vtt"
    p.write_text(VTT, encoding="utf-8")
    return p


def test_vtt_parsing_segments_ms(vtt_file: Path):
    segs = parse_caption_file(vtt_file, video_id="vid1")
    assert len(segs) == 8
    assert segs[0].start_ms == 0
    assert segs[0].end_ms == 4000
    assert segs[2].start_ms == 9500
    assert segs[2].end_ms == 15000
    assert segs[-1].start_ms == (70 * 60 + 5) * 1000
    assert all(s.video_id == "vid1" for s in segs)


def _seg(start_s, end_s, text):
    return Segment(
        text=text, source_type="video", video_id="v",
        start_ms=int(start_s * 1000), end_ms=int(end_s * 1000),
    )


def test_chunker_window_sizes_and_no_mid_sentence_split():
    segs = []
    t = 0.0
    for i in range(40):
        segs.append(_seg(t, t + 2.0, f"Sentence number {i} ends here."))
        t += 2.0
    chunks = chunk_transcript(segs, "res1", "course1", embed_model="hashing-v1")
    assert len(chunks) >= 2
    for c in chunks[:-1]:
        dur = (c.end_ms - c.start_ms) / 1000.0
        assert dur >= 30.0
        assert c.text.rstrip().endswith(".")
    # no sentence is split across chunks: every chunk ends cleanly
    for c in chunks:
        assert "Sentence number" in c.text


def test_chunker_overlap():
    segs = []
    t = 0.0
    for i in range(60):
        segs.append(_seg(t, t + 2.0, f"Point {i} is made here."))
        t += 2.0
    chunks = chunk_transcript(
        segs, "res1", "course1", min_window_s=30.0, max_window_s=60.0, overlap_s=12.0
    )
    assert len(chunks) >= 2
    import itertools

    for prev, cur in itertools.pairwise(chunks):
        assert cur.start_ms is not None and prev.end_ms is not None
        assert cur.start_ms < prev.end_ms  # overlap exists


def test_chunker_force_closes_unpunctuated_transcript():
    # ASR output with no sentence-ending punctuation must still yield
    # bounded chunks (regression: 211 unpunctuated segments -> 1 giant chunk
    # that the end_ms gate then excluded for every T before the video end).
    segs = [_seg(i * 10.0, (i + 1) * 10.0, f"fragment number {i} without stop") for i in range(40)]
    chunks = chunk_transcript(segs, "res1", "course1", embed_model="hashing-v1")
    assert len(chunks) >= 2
    for c in chunks:
        assert (c.end_ms - c.start_ms) / 1000.0 <= 60.0 * 1.5 + 10.0


def test_chunk_ids_deterministic():
    segs = [_seg(i * 3, i * 3 + 2.9, f"Fact {i} is true.") for i in range(40)]
    a = chunk_transcript(segs, "res1", "course1")
    b = chunk_transcript(segs, "res1", "course1")
    assert [c.id for c in a] == [c.id for c in b]
    assert len({c.id for c in a}) == len(a)


def test_hashing_embedder_deterministic_and_dim():
    e = HashingEmbedder()
    v1 = e.embed("hello world vectors")
    v2 = e.embed("hello world vectors")
    assert v1 == v2
    assert len(v1) == 1536
    assert e.name == "hashing-v1"


def test_openai_embedder_requires_key(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "embedding_api_key", "")
    emb = OpenAIEmbedder(api_key="")
    with pytest.raises(RuntimeError):
        emb.embed("hi")


def test_run_ingestion_dry_run(vtt_file: Path):
    row = {"id": "res1", "course_id": "c1", "type": "video", "video_id": "vid1"}
    chunks = asyncio.run(
        run_ingestion(row, vtt_file, embedder=HashingEmbedder(), database_url="")
    )
    assert chunks
    assert all(c.embed_model == "hashing-v1" for c in chunks)
    assert all(c.embedding is not None and len(c.embedding) == 1536 for c in chunks)
    assert all(c.resource_id == "res1" and c.course_id == "c1" for c in chunks)


def test_pdf_extractor(tmp_path: Path):
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "First page of the PDF.")
    page2 = doc.new_page()
    page2.insert_text((72, 72), "Second page content here.")
    p = tmp_path / "doc.pdf"
    doc.save(str(p))
    segs = extract_pdf(p)
    assert len(segs) == 2
    assert segs[0].page_no == 1
    assert segs[1].page_no == 2


def test_pptx_extractor(tmp_path: Path):
    pytest.importorskip("pptx")
    from pptx import Presentation
    from pptx.util import Inches

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[5])
    slide.shapes.title.text = "Slide One Title"
    slide2 = prs.slides.add_slide(prs.slide_layouts[5])
    tx = slide2.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1))
    tx.text_frame.text = "Slide two body text."
    p = tmp_path / "deck.pptx"
    prs.save(str(p))
    segs = extract_pptx(p)
    assert len(segs) == 2
    assert segs[0].slide_no == 1
    assert segs[1].slide_no == 2


def test_extract_text(tmp_path: Path):
    p = tmp_path / "notes.md"
    p.write_text("Para one.\n\nPara two with more text.", encoding="utf-8")
    segs = extract(p, "note")
    assert len(segs) == 2
    assert segs[0].source_type == "note"


# --- Duration alignment: the anti-spoiler guard --------------------------------
#
# Background: Gemini claimed ~1.5x the true duration of a 655.53s lecture across
# four independent runs (1109.0s, 998.4s, 974.7s, 981.4s). The strict-T gate
# (`end_ms <= T`) is the product's no-spoiler guarantee, so wrong timestamps
# silently break it. Root cause: the local-file call site never passed
# duration_s, so every duration guard was skipped.

REAL_MP4 = (
    Path(__file__).resolve().parents[3]
    / "test-data"
    / "Partial derivatives, introduction.mp4"
)


def _speech_payload(duration_s: float, scale: float = 1.0) -> list[dict]:
    """Contiguous 10s speech segments spanning `duration_s * scale`."""
    segments = []
    n = round(duration_s * scale / 10.0) or 1
    for i in range(n):
        segments.append(
            {
                "start_s": float(i * 10),
                "end_s": float((i + 1) * 10),
                "text": f"segment {i} of the lecture",
            }
        )
    return segments


def test_overshooting_transcript_is_rejected():
    """A transcript claiming 1.5x the media duration must fail validation."""
    duration = 655.53
    payload = _speech_payload(duration, scale=1.5)
    # 655.53 * 1.5 = 983.3, rounded to whole 10s segments.
    assert max(s["end_s"] for s in payload) >= duration * 1.45

    with pytest.raises(ValueError, match="refusing to index"):
        validate_transcript_payload(payload, duration_s=duration)


def test_overshooting_transcript_fails_the_ingest_job(tmp_path: Path, monkeypatch):
    """End to end: a drifted transcript must fail the job, index nothing.

    This is the assertion that matters for the product. Rejecting inside a
    helper is worthless if the job still marks the resource 'done'.
    """
    from app.ingestion import router
    from app.retrieval import registry

    asset = tmp_path / "lecture.mp4"
    asset.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 16)

    indexed: list = []
    removed: list = []

    class _Store:
        def remove_resource(self, rid):
            removed.append(rid)

        def index(self, chunks):
            indexed.extend(chunks)

    class _Embedder:
        name = "test"

        def embed_batch(self, texts):
            return [[0.0] for _ in texts]

    monkeypatch.setattr(router, "get_embedder", lambda: _Embedder())
    monkeypatch.setattr(registry, "get_shared_store", lambda: _Store())
    monkeypatch.setattr(registry, "save_snapshot", lambda: None)
    monkeypatch.setattr(router, "save_ingest_state", lambda: None)
    monkeypatch.setattr(
        router,
        "probe_media_duration_s",
        lambda p: 655.53,
    )

    def _boom(*_args, **_kwargs):
        raise ValueError("transcript claims 983.0s but the media is only 655.5s")

    monkeypatch.setattr(router, "transcribe_with_gemini", _boom)

    job_id, rid = "job1", "res1"
    router._jobs[job_id] = {}
    router._resources[rid] = {}
    router._finish_job_files(job_id, "c1", rid, asset, "video", "Lecture")

    assert router._jobs[job_id]["status"] == "failed"
    assert indexed == []
    assert removed == []


def test_duration_within_tolerance_is_accepted():
    """A small overshoot is self-corrected rather than rejected."""
    duration = 655.53
    payload = _speech_payload(duration, scale=1.0 + MAX_DURATION_OVERSHOOT_RATIO / 2)
    cleaned = validate_transcript_payload(payload, duration_s=duration)

    assert cleaned, "a within-tolerance transcript must still be accepted"
    # Corrected, not merely tolerated: nothing may end past the real duration.
    assert max(s["end_s"] for s in cleaned) <= duration + 1e-6


def test_clamping_never_increases_timestamps():
    """SAFETY DIRECTION: a clamped timestamp is never later than its original.

    If this ever fails, the correction could move content forward and expose
    unreached material -- a spoiler. It must only ever move content earlier.
    """
    duration = 655.53
    payload = _speech_payload(duration, scale=1.08)
    clamped = clamp_segments_to_duration(payload, duration)

    assert clamped is not payload, "clamping must not mutate the input"
    assert len(clamped) == len(payload)
    for original, corrected in zip(payload, clamped):
        assert corrected["start_s"] <= original["start_s"]
        assert corrected["end_s"] <= original["end_s"]
    assert max(s["end_s"] for s in clamped) <= duration

    # Monotonic and order preserving: the validator's no-overlap/no-gap
    # contract must survive the rescale.
    for prev, cur in itertools.pairwise(clamped):
        assert cur["start_s"] >= prev["start_s"]
        assert cur["start_s"] <= prev["end_s"] + 2.0


def test_local_file_path_passes_duration(tmp_path: Path, monkeypatch):
    """REGRESSION GUARD: the router must supply a measured duration.

    This is the omission that let every duration guard sleep. If duration_s is
    ever dropped here again, the overshoot tests above keep passing while the
    production path silently goes unchecked.
    """
    from app.ingestion import router
    from app.retrieval import registry

    asset = tmp_path / "lecture.mp4"
    asset.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 16)

    captured: dict = {}

    def _capture(media_path, title, duration_s=None):
        captured["duration_s"] = duration_s
        return []

    class _Store:
        def remove_resource(self, rid):
            pass

        def index(self, chunks):
            pass

    class _Embedder:
        name = "test"

        def embed_batch(self, texts):
            return [[0.0] for _ in texts]

    monkeypatch.setattr(router, "get_embedder", lambda: _Embedder())
    monkeypatch.setattr(registry, "get_shared_store", lambda: _Store())
    monkeypatch.setattr(registry, "save_snapshot", lambda: None)
    monkeypatch.setattr(router, "save_ingest_state", lambda: None)
    monkeypatch.setattr(router, "transcribe_with_gemini", _capture)
    monkeypatch.setattr(router, "probe_media_duration_s", lambda p: 655.53)

    job_id, rid = "job2", "res2"
    router._jobs[job_id] = {}
    router._resources[rid] = {}
    router._finish_job_files(job_id, "c1", rid, asset, "video", "Lecture")

    assert captured["duration_s"] == 655.53
    assert router._jobs[job_id]["status"] == "done"


def test_duration_guard_absent_is_loud(caplog):
    """No measurable duration => accept WITH A WARNING, never silently.

    Explicitly chosen over rejecting: formats this parser cannot read (mkv,
    webm, ...) must still ingest, and the strict-T gate stays correct because
    it keys off the timestamps themselves. But the condition is loud, because
    an unmeasured duration is exactly what hid a 1.5x drift.
    """
    payload = _speech_payload(655.53, scale=1.5)

    with caplog.at_level(logging.WARNING, logger="app.ingestion.transcribe"):
        result = enforce_duration_alignment(payload, None)

    assert result == payload, "payload must pass through untouched"
    assert any("duration guard inactive" in r.message for r in caplog.records)


@pytest.mark.skipif(not REAL_MP4.exists(), reason="sample lecture not present")
def test_probe_measures_real_media_duration():
    """The measurement the whole guard depends on, checked against real bytes.

    655.53s, read from the fragmented-MP4 boxes (mvhd is 0 for this file).
    Gemini claimed 981.4s for it, so the true duration must come from the
    container, never from the model.
    """
    duration = probe_media_duration_s(REAL_MP4)
    assert duration is not None
    assert duration == pytest.approx(655.53, abs=0.5)


@pytest.mark.skipif(not REAL_MP4.exists(), reason="sample lecture not present")
def test_probe_returns_none_for_unreadable_media(tmp_path: Path):
    """Never guesses: unprobeable media yields None so callers can shout."""
    bogus = tmp_path / "broken.mp4"
    bogus.write_bytes(b"not an mp4 at all")
    assert probe_media_duration_s(bogus) is None
    assert probe_media_duration_s(tmp_path / "missing.mp4") is None
    # Non-MP4 containers are out of scope for the box parser.
    other = tmp_path / "lecture.mkv"
    other.write_bytes(b"\x00" * 64)
    assert probe_media_duration_s(other) is None
