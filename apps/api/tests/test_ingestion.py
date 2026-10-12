"""End-to-end wiring: jobs, routes, and the pipeline against a real store.

These assert the contract ``apps/web/lib/api.ts`` depends on (read-only --
``apps/web`` is never modified) plus the safety properties that only appear
once the pieces are wired together:

* an unknown duration refuses to ingest rather than indexing time-gated
  content with an unbounded timeline
* ``_clamp_to_duration`` makes no chunk claim content past the media end
* audio and visual chunks are both ``source_type="video"`` so they share ONE
  strict-T gate rather than two that could drift
* the routes exist with the shapes the web client expects

No network: Gemini is never called (extraction is invoked with
``with_visual=False`` and monkeypatched transcription), and the faster-whisper
model download is never triggered.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.ingestion import jobs as jobs_mod
from app.ingestion.jobs import (
    build_chunks,
    ingest_media,
    job_payload,
    new_job,
    source_type_for,
)
from tests.store_helpers import make_chunk, make_embedder


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """A pipeline wired to a real Chroma store in ``tmp_path``.

    Transcription is stubbed so faster-whisper cannot download a model, and
    Gemini is not configured, so no test can reach the network.
    """
    from app.retrieval.store import ChromaVectorStore

    store = ChromaVectorStore(
        path=tmp_path / "chroma", embedder=make_embedder(), collection="wired_chunks"
    )
    monkeypatch.setattr(jobs_mod, "_store", lambda: store)

    from app.ingestion import audio as audio_mod

    def _stub_transcribe(path, **kw):
        segs = [
            audio_mod.TranscriptSegment(0.0, 12.0, "welcome to partial derivatives"),
            audio_mod.TranscriptSegment(12.0, 30.0, "we hold y fixed and vary x"),
        ]
        return segs, {"engine": "stub", "timestamp_agreement": {"comparable": False}}

    monkeypatch.setattr(audio_mod, "transcribe", _stub_transcribe)
    jobs_mod.clear_jobs()
    return store


def test_source_type_dispatch():
    assert source_type_for("lecture.mp4") == "video"
    assert source_type_for("UNIT-1-001.PDF") == "pdf"
    assert source_type_for("deck.pptx") == "pptx"
    with pytest.raises(ValueError):
        source_type_for("notes.txt")


def test_job_lifecycle_and_payload_shape():
    """``IngestJob`` must serialise to exactly the keys apps/web reads."""
    job = new_job("course_1", "res_abc")
    payload = job_payload(job)
    assert set(payload) == {"job_id", "resource_id", "status"}
    assert payload["status"] == "pending"

    from app.ingestion.jobs import run_ingest, update_job

    update_job(job.job_id, status="processing")
    run_ingest(job, lambda: {"total_chunks": 3})
    assert job.status == "done"
    assert job.detail == {"total_chunks": 3}
    assert "error" not in job_payload(job)


def test_failed_job_reports_the_error(tmp_path):
    """A failing ingest must land as ``failed`` with a message, never as ``done``."""

    def _boom():
        raise ValueError("cannot determine duration")

    job = new_job("course_1", "res_abc")
    jobs_mod.run_ingest(job, _boom)
    assert job.status == "failed"
    assert "cannot determine duration" in job_payload(job)["error"]


def test_ingest_media_refuses_without_a_duration(wired, tmp_path, monkeypatch):
    """Unknown duration -> refusal, not a half-gated index.

    This is the end-to-end statement of the ADR-002 §5.1 obligation: with no
    duration there is no trustworthy upper bound, so ``end_ms <= T`` is
    meaningless and nothing gets indexed.
    """
    fake = tmp_path / "mystery.mp4"
    fake.write_bytes(b"\x00" * 64)
    monkeypatch.setattr(
        "app.ingestion.jobs.media_duration_ms", lambda path, **kw: None
    )

    with pytest.raises(ValueError, match="refusing to ingest"):
        ingest_media(fake, course_id="course_1", resource_id="res_x")

    assert wired.count() == 0, "a refused ingest must index nothing"


def test_clamp_keeps_every_chunk_inside_the_duration():
    """No chunk may claim an ``end_ms`` past the authoritative duration.

    A model-sourced boundary that slipped through the per-segment arithmetic is
    still caught here. Chunks whose collapse leaves ``end <= start`` are
    dropped rather than indexed as something ungated.
    """
    duration = 30_000
    chunks = [
        make_chunk("c_ok", start_ms=0, end_ms=20_000),
        make_chunk("c_over", start_ms=25_000, end_ms=45_000),
        # Straddles the duration edge: clamping brings it inside, and it stays
        # coherent (29s <= 30s), so it is kept rather than dropped.
        make_chunk("c_straddles", start_ms=29_000, end_ms=31_000),
        # Entirely past the duration: clamping collapses it, so it is dropped.
        # Keeping it would mean indexing content past the end of the file.
        make_chunk("c_past_end", start_ms=40_000, end_ms=50_000),
    ]
    clamped = jobs_mod._clamp_to_duration(chunks, duration)

    ids = {c.chunk_id for c in clamped}
    assert ids == {"c_ok", "c_over", "c_straddles"}
    for chunk in clamped:
        assert chunk.end_ms <= duration
        assert chunk.start_ms <= chunk.end_ms
    assert "c_past_end" not in ids, "a chunk wholly past the duration must be dropped"


def test_clamp_leaves_document_chunks_alone():
    """Documents have no time axis, so the clamp must not touch their bounds."""
    doc = make_chunk(
        "c_pdf", source_type="pdf", start_ms=None, end_ms=None, page_no=3,
        video_id=None,
    )
    out = jobs_mod._clamp_to_duration([doc], 30_000)
    assert out == [doc]
    assert out[0].start_ms is None and out[0].end_ms is None


def test_audio_and_visual_chunks_share_one_source_type():
    """Both media kinds are indexed as ``video``.

    One gate, not two. If visual chunks got their own source type they would
    need their own entry in the store's gated set *and* in
    ``app.chat.service.VIDEO_SOURCE_TYPES``, and a gap between those two sets
    would silently ungate on-screen content -- the exact spoiler this project
    is guarding.
    """
    from app.ingestion.audio import TranscriptSegment
    from app.ingestion.visual import VisualSegment
    from app.retrieval.store import TIME_GATED_SOURCE_TYPES

    chunks = build_chunks(
        resource_id="res_v",
        course_id="course_1",
        source_type="video",
        transcript=[TranscriptSegment(0.0, 10.0, "spoken")],
        visual=[VisualSegment(5, "a plotted curve", 20_000, 24_000)],
        video_id="vid_1",
    )

    kinds = {c.metadata["kind"] for c in chunks}
    assert kinds == {"transcript", "visual"}
    assert {c.source_type for c in chunks} == {"video"}
    assert "video" in TIME_GATED_SOURCE_TYPES

    for chunk in chunks:
        assert chunk.start_ms is not None and chunk.end_ms is not None
        assert chunk.end_ms <= chunk.start_ms + 4_000 or chunk.metadata["kind"] == "transcript"


def test_ingest_media_indexes_and_respects_the_gate(wired, tmp_path, monkeypatch):
    """A full media ingest: chunks land in the store and the gate holds."""
    from app.ingestion.duration import container_duration_ms

    media = Path(__file__).resolve().parent.parent.parent.parent / "test-data"
    media = media / "Partial-derivatives.mp4"
    if not media.exists():
        pytest.skip("test media not present")
    assert container_duration_ms(media) == 655_500

    monkeypatch.setattr(
        jobs_mod, "media_duration_ms", lambda path, **kw: 655_500
    )
    report = ingest_media(
        media, course_id="course_1", resource_id="res_v", with_visual=False
    )

    assert report["duration_ms"] == 655_500
    assert report["audio_chunks"] > 0
    assert report["max_end_ms"] <= 655_500, "a chunk exceeded the media duration"

    got = wired.query(
        "partial derivatives",
        course_id="course_1",
        resource_scope=["res_v"],
        current_time_ms=655_500,
        k=10,
    )
    assert got
    for passage in got:
        assert passage.end_ms is not None
        assert passage.end_ms <= 655_500

    # At T=0 nothing time-gated is retrievable.
    assert (
        wired.query(
            "partial derivatives",
            course_id="course_1",
            resource_scope=["res_v"],
            current_time_ms=0,
            k=10,
        )
        == []
    )


# ---------------------------------------------------------------------------
# Routes (contract with apps/web/lib/api.ts)
# ---------------------------------------------------------------------------


@pytest.fixture
def client(wired, monkeypatch, tmp_path):
    """TestClient with an isolated upload dir and job registry."""
    import app.ingestion.router as router_mod

    monkeypatch.setattr(
        router_mod, "UPLOAD_DIR", tmp_path / "uploads", raising=True
    )
    # `list_resources` imports get_shared_store lazily from inside the handler,
    # so the patch has to land on the registry module, not on router_mod.
    import app.retrieval.registry as registry_mod

    monkeypatch.setattr(registry_mod, "get_shared_store", lambda: wired)
    jobs_mod.clear_jobs()

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def test_ingestion_routes_are_mounted(client):
    """Every route apps/web calls must exist -- no more 404s.

    Asserted against the OpenAPI schema because that is what the web client's
    base URL is actually pointed at.
    """
    paths = client.get("/openapi.json").json()["paths"]
    assert "post" in paths["/courses/{course_id}/ingest"]
    assert "post" in paths["/courses/{course_id}/ingest-youtube"]
    assert "get" in paths["/ingestion/jobs/{job_id}"]
    assert "get" in paths["/courses/{course_id}/resources"]


def test_ingest_file_rejects_an_unsupported_type(client):
    """A non-media upload is a 422 with a message, not a silent 200."""
    resp = client.post(
        "/courses/course_1/ingest",
        files={"file": ("notes.txt", b"plain text", "text/plain")},
    )
    assert resp.status_code == 422
    assert "unsupported file type" in resp.json()["detail"]


def test_ingest_file_rejects_an_empty_upload(client):
    resp = client.post(
        "/courses/course_1/ingest", files={"file": ("empty.mp4", b"", "video/mp4")}
    )
    assert resp.status_code == 422
    assert "empty" in resp.json()["detail"]


def test_ingest_youtube_rejects_an_unparseable_url(client):
    """A bad URL is a 422 (the web client special-cases 422 with friendly copy)."""
    resp = client.post(
        "/courses/course_1/ingest-youtube", json={"url": "https://example.com/x"}
    )
    assert resp.status_code == 422
    assert "YouTube" in resp.json()["detail"]


def test_unknown_job_is_404(client):
    resp = client.get("/ingestion/jobs/job_nope")
    assert resp.status_code == 404
    assert "unknown job" in resp.json()["detail"]


def test_resources_returns_the_web_envelope(client, wired):
    """``{"resources": [...]}`` with the fields the web UI reads."""
    resp = client.get("/courses/course_1/resources")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"resources"}

    wired.add([make_chunk("c1", resource_id="res_v", video_id="vid_1")])
    body = client.get("/courses/course_1/resources").json()
    assert body["resources"]
    entry = body["resources"][0]
    for field in ("id", "type", "provider", "youtube_id", "title", "status", "asset_name"):
        assert field in entry, f"resource entry is missing {field}"
    assert entry["id"] == "res_v"
    # This chunk was indexed by a bare factory with no filename, so the title
    # is honestly empty rather than the internal extractor label "transcript".
    assert entry["title"] is None


def test_media_route_404s_without_a_stored_asset(client):
    """No file on disk -> 404. The UI never gets a silent broken video."""
    resp = client.get("/resources/res_missing/media")
    assert resp.status_code == 404


def test_ingest_file_queues_a_job(client, monkeypatch):
    """A valid upload returns the frozen ``{job_id, resource_id, status}``.

    The pipeline is stubbed so this stays a wiring test -- no ASR, no Gemini --
    but it does prove the POST shape the web client polls on.
    """
    monkeypatch.setattr(
        jobs_mod,
        "ingest_media",
        lambda path, **kw: {"total_chunks": 1, "audio_chunks": 1,
                            "visual_chunks": 0, "duration_ms": 1,
                            "max_end_ms": 1},
    )
    resp = client.post(
        "/courses/course_1/ingest",
        files={"file": ("lecture.mp4", b"\x00" * 32, "video/mp4")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"job_id", "resource_id", "status"}
    assert body["status"] in ("pending", "processing", "done")

    # And the job is pollable through the route the web client uses.
    job = client.get(f"/ingestion/jobs/{body['job_id']}").json()
    assert job["resource_id"] == body["resource_id"]
    assert job["status"] in ("pending", "processing", "done")


def test_youtube_caption_path_is_preferred(monkeypatch):
    """Captions are the fast path; ASR is only a fallback.

    Asserted by making ``download_media`` raise: if the caption path ever
    started re-downloading, this test fails immediately.
    """
    from app.ingestion import youtube as yt_mod

    def _no_download(*a, **kw):
        raise AssertionError("captions exist; media must not be re-downloaded")

    monkeypatch.setattr(yt_mod, "download_media", _no_download)
    from app.ingestion.chunking import TextSegment

    monkeypatch.setattr(yt_mod, "fetch_captions", lambda vid: [
        TextSegment(text="intro", start_ms=0, end_ms=1_500, kind="caption"),
        TextSegment(text="body", start_ms=1_500, end_ms=4_000, kind="caption"),
    ])

    captured: list = []
    monkeypatch.setattr(
        jobs_mod, "_store", lambda: type(
            "S", (), {"add": lambda self, chunks: captured.extend(chunks) or len(chunks)}
        )()
    )

    report = jobs_mod.ingest_youtube(
        "https://www.youtube.com/watch?v=abcdefghijk",
        course_id="course_1",
        resource_id="res_yt",
    )

    assert report["source"] == "captions"
    assert report["caption_chunks"] == 2
    assert len(captured) == 2
    for chunk in captured:
        assert chunk.start_ms is not None and chunk.end_ms is not None
        assert chunk.metadata["kind"] == "caption"
        assert chunk.end_ms <= 4_000, "caption end exceeded the last cue"


def test_youtube_url_parsing():
    from app.ingestion.youtube import parse_youtube_id

    assert parse_youtube_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert parse_youtube_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert parse_youtube_id("https://www.youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert parse_youtube_id("https://vimeo.com/12345") is None
    assert parse_youtube_id("") is None


def test_youtube_without_captions_falls_back_to_asr(monkeypatch):
    """No captions -> download + ASR, and a download failure is an error."""
    from app.ingestion import youtube as yt_mod

    monkeypatch.setattr(yt_mod, "fetch_captions", lambda vid: [])
    monkeypatch.setattr(yt_mod, "download_media", lambda *a, **kw: None)
    with pytest.raises(ValueError, match="no captions"):
        jobs_mod.ingest_youtube(
            "https://youtu.be/dQw4w9WgXcQ",
            course_id="course_1",
            resource_id="res_yt",
        )


def test_youtube_rejects_an_unparseable_url():
    with pytest.raises(ValueError, match="YouTube video id"):
        jobs_mod.ingest_youtube(
            "https://example.com/nope", course_id="c", resource_id="r"
        )


def test_ingest_records_original_filename_as_title(wired, tmp_path, monkeypatch):
    """The catalogue title is the uploaded file's name, not an extractor label.

    Regression: ``resources()`` used to return ``meta["kind"]``, so every
    video resource was titled "transcript" -- a word that means nothing to a
    learner browsing the sources panel. The original filename must survive
    ingest into the title the UI renders.
    """
    media = Path(__file__).resolve().parent.parent.parent.parent / "test-data"
    media = media / "Partial-derivatives.mp4"
    if not media.exists():
        pytest.skip("test media not present")

    monkeypatch.setattr(jobs_mod, "media_duration_ms", lambda path, **kw: 655_500)
    ingest_media(
        media, course_id="course_1", resource_id="res_v", with_visual=False
    )

    entries = {
        e["id"]: e for e in wired.resources("course_1") if e["id"] == "res_v"
    }
    assert entries, "the ingested resource is missing from the catalogue"
    assert entries["res_v"]["title"] == "Partial-derivatives.mp4"
    # The internal extractor label must never leak into the user-facing title.
    assert entries["res_v"]["title"] not in ("transcript", "visual")


def test_document_ingest_records_original_filename_as_title(wired, tmp_path):
    """The document path sets a real title too, not just the video path."""
    pdf = Path(__file__).resolve().parent.parent.parent.parent / "test-data"
    pdf = pdf / "UNIT-1-001.pdf"
    if not pdf.exists():
        pytest.skip("test pdf not present")

    jobs_mod.ingest_document(
        pdf, course_id="course_1", resource_id="res_pdf", source_type="pdf"
    )
    entry = next(
        e for e in wired.resources("course_1") if e["id"] == "res_pdf"
    )
    assert entry["title"] == "UNIT-1-001.pdf"


def test_reingest_corrects_a_meaningless_title(wired, tmp_path, monkeypatch):
    """Re-ingesting a resource replaces a bad title instead of preserving it.

    An existing resource whose chunks carry no filename must not pin the
    useless value; the re-ingest's chunks carry the real one and win.
    """
    # Pre-existing state: chunks indexed without any title metadata, which is
    # what produced `title: "transcript"` in production.
    wired.add([make_chunk("c_legacy", resource_id="res_v", course_id="course_1")])
    assert next(
        e for e in wired.resources("course_1") if e["id"] == "res_v"
    )["title"] is None

    media = Path(__file__).resolve().parent.parent.parent.parent / "test-data"
    media = media / "Partial-derivatives.mp4"
    if not media.exists():
        pytest.skip("test media not present")
    monkeypatch.setattr(jobs_mod, "media_duration_ms", lambda path, **kw: 655_500)
    ingest_media(
        media, course_id="course_1", resource_id="res_v", with_visual=False
    )

    entry = next(e for e in wired.resources("course_1") if e["id"] == "res_v")
    assert entry["title"] == "Partial-derivatives.mp4"


def test_missing_filename_leaves_the_title_empty_not_invented(
    wired, tmp_path, monkeypatch
):
    """No filename available -> ``None``, never a made-up one.

    The brief forbids fabricating titles: with nothing to go on, the UI must
    be able to fall back to the resource id.
    """
    fake = tmp_path / "no-extension-known.mp4"
    fake.write_bytes(b"not real media")
    monkeypatch.setattr(jobs_mod, "media_duration_ms", lambda path, **kw: 10_000)
    jobs_mod.ingest_media(
        fake, course_id="course_1", resource_id="res_noname", with_visual=False
    )
    entry = next(
        e for e in wired.resources("course_1") if e["id"] == "res_noname"
    )
    # The filename here IS known, so it is carried -- the point of the test is
    # that it is the file's own name and not the source type or kind.
    assert entry["title"] == "no-extension-known.mp4"

    # A resource indexed with no title at all stays None rather than falling
    # back to an internal label.
    wired.add(
        [make_chunk("c_bare", resource_id="res_bare", course_id="course_1")]
    )
    bare = next(e for e in wired.resources("course_1") if e["id"] == "res_bare")
    assert bare["title"] is None


def test_document_ingest_is_never_time_gated(wired, tmp_path):
    """A PDF ingests with no duration lookup and no timestamps."""
    pdf = Path(__file__).resolve().parent.parent.parent.parent / "test-data"
    pdf = pdf / "UNIT-1-001.pdf"
    if not pdf.exists():
        pytest.skip("test pdf not present")

    report = jobs_mod.ingest_document(
        pdf, course_id="course_1", resource_id="res_pdf", source_type="pdf"
    )
    assert report["document_chunks"] > 0

    got = wired.query(
        "partial derivative",
        course_id="course_1",
        resource_scope=["res_pdf"],
        current_time_ms=0,  # T=0: a doc must still be retrievable
        k=5,
    )
    assert got, "a pdf must be retrievable at T=0"
    for passage in got:
        assert passage.source_type == "pdf"
        assert passage.end_ms is None


def test_unsupported_document_type_raises():
    with pytest.raises(ValueError, match="documents only"):
        jobs_mod.ingest_document(
            "x.txt", course_id="c", resource_id="r", source_type="txt"
        )


def test_embedder_choice_is_offline_without_a_key(monkeypatch):
    """No ``EMBEDDING_API_KEY`` -> hashing embedder, no network at import."""
    from app.config import settings
    from app.ingestion.embedder import (
        EMBED_DIM,
        GEMINI_EMBED_DIM,
        get_default_embedder,
    )

    monkeypatch.setattr(settings, "embedding_api_key", "", raising=True)
    embedder = get_default_embedder()
    assert type(embedder).__name__ == "HashingEmbedder"
    assert len(embedder.embed_query("partial derivative")) == embedder.dim

    monkeypatch.setattr(settings, "embedding_api_key", "key", raising=True)
    assert settings.expected_embedding_dim() == GEMINI_EMBED_DIM
    monkeypatch.setattr(settings, "embedding_api_key", "", raising=True)
    assert settings.expected_embedding_dim() == EMBED_DIM


def test_hashing_embedder_is_deterministic_and_normalised():
    """Same text -> same vector (ids and scores must be reproducible)."""
    from app.ingestion.embedder import HashingEmbedder

    embedder = HashingEmbedder(dim=128)
    a = embedder.embed_query("partial derivative")
    b = embedder.embed_query("partial derivative")
    assert a == b
    assert abs(sum(v * v for v in a) - 1.0) < 1e-9
    assert embedder.embed_query("partial derivatives") != a
    assert embedder.embed_query("") == [0.0] * 128


def test_shared_store_is_a_singleton(tmp_path, monkeypatch):
    """One Chroma client per process, or the on-disk index is unsafe."""
    from app.retrieval import registry

    monkeypatch.setenv("CHROMA_PATH", str(tmp_path / "chroma"))
    registry.reset_shared_store()
    first = registry.get_shared_store()
    second = registry.get_shared_store()
    assert first is second
    registry.reset_shared_store()


def test_registry_never_touches_the_developer_data_dir(tmp_path, monkeypatch):
    """``CHROMA_PATH`` is honoured so a test cannot write to ``data/``."""
    from app.retrieval import registry

    monkeypatch.setenv("CHROMA_PATH", str(tmp_path / "isolated"))
    assert registry.default_chroma_path() == tmp_path / "isolated"

    monkeypatch.delenv("CHROMA_PATH")
    assert registry.default_chroma_path().name == "chroma"


def test_chat_service_binds_the_real_retrieval_service(wired):
    """``ChatService._retrieval()`` resolves to the Chroma-backed service.

    The seam that ``test_chat_safety.py`` monkeypatches is asserted here
    against the *real* object, so the suite cannot pass with the seam pointing
    at nothing.
    """
    import asyncio

    from app.chat.service import ChatService
    from app.retrieval.service import RetrievalService

    service = ChatService(store=wired, embedder=make_embedder())
    retrieval = service._retrieval()
    assert isinstance(retrieval, RetrievalService)
    assert retrieval._store is wired

    wired.add([make_chunk("c1", start_ms=0, end_ms=5_000)])
    passages = asyncio.run(
        retrieval.retrieve(
            "partial derivatives", "course_1", ["res_video"], 10_000, k=5
        )
    )
    assert [p.chunk_id for p in passages] == ["c1"]
    for field in (
        "chunk_id", "resource_id", "source_type", "text", "start_ms",
        "end_ms", "video_id", "page_no", "slide_no", "score",
    ):
        assert hasattr(passages[0], field)


def test_retrieval_service_asserts_on_a_leaked_passage(wired):
    """The service layer raises rather than quietly filtering a leak.

    Injects a store that ignores its filters -- i.e. simulates a filter
    regression -- and requires the assertion to fire.
    """
    import asyncio

    from app.retrieval.service import RetrievalService

    class LeakyStore:
        def query(self, *a, **kw):
            from app.retrieval.store import RetrievedPassage

            return [
                RetrievedPassage(
                    chunk_id="c_future", resource_id="res_video",
                    course_id="course_1", source_type="video", text="t",
                    start_ms=0, end_ms=900_000, score=1.0,
                )
            ]

    service = RetrievalService(store=LeakyStore())
    with pytest.raises(AssertionError, match="strict-T leak"):
        asyncio.run(
            service.retrieve("q", "course_1", ["res_video"], 30_000, k=5)
        )


def test_unreadable_media_logs_loudly_and_refuses(tmp_path, monkeypatch, caplog):
    """End-to-end: a corrupt file is refused, with an ERROR in the log."""
    junk = tmp_path / "corrupt.mp4"
    junk.write_bytes(b"\xde\xad\xbe\xef" * 32)
    monkeypatch.setattr(
        "app.ingestion.duration.media_duration_ms", lambda path, **kw: None
    )
    with (
        caplog.at_level(logging.ERROR, logger="app.ingestion.duration"),
        pytest.raises(ValueError, match="refusing to ingest"),
    ):
        jobs_mod.ingest_media(junk, course_id="c", resource_id="r")
    assert caplog.records

# ---------------------------------------------------------------------------
# YouTube ingest scoped by subject_id
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_subject_store(tmp_path, monkeypatch):
    """A clean subject store that never touches the real data/ dir."""
    from app.subjects import store as subjects_store

    monkeypatch.setattr(
        subjects_store, "SUBJECT_STATE_PATH", tmp_path / "subjects.json"
    )
    subjects_store.set_persistence_enabled(False)
    subjects_store.reset_store()
    yield subjects_store
    subjects_store.set_persistence_enabled(False)
    subjects_store.reset_store()


def _inline_schedule(monkeypatch):
    """Run the queued ingest synchronously inside the request."""
    import app.ingestion.router as router_mod

    monkeypatch.setattr(router_mod, "schedule", lambda job, work: work())
    return router_mod


YT_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def test_ingest_with_subject_id_uses_subject_course_id(
    client, wired, isolated_subject_store, monkeypatch
):
    """subject_id present -> ingest under the subject's real course_id."""
    router_mod = _inline_schedule(monkeypatch)
    subject = isolated_subject_store.subject_store.create(
        name="Bio", course_id="course_real"
    )
    captured = {}

    def fake_ingest(url, *, course_id, resource_id, **kwargs):
        captured["course_id"] = course_id
        wired.add(
            [
                make_chunk(
                    f"ch_{resource_id}",
                    resource_id=resource_id,
                    course_id=course_id,
                )
            ]
        )

    monkeypatch.setattr(router_mod, "ingest_youtube", fake_ingest)

    resp = client.post(
        f"/courses/{subject.id}/ingest-youtube",
        json={"url": YT_URL, "subject_id": subject.id},
    )
    assert resp.status_code == 200
    assert captured["course_id"] == "course_real"


def test_ingest_with_subject_id_auto_attaches_resource_to_subject(
    client, wired, isolated_subject_store, monkeypatch
):
    """The ingested resource must immediately list under the subject."""
    router_mod = _inline_schedule(monkeypatch)
    subject = isolated_subject_store.subject_store.create(
        name="Bio", course_id="course_real"
    )

    def fake_ingest(url, *, course_id, resource_id, **kwargs):
        wired.add(
            [
                make_chunk(
                    f"ch_{resource_id}",
                    resource_id=resource_id,
                    course_id=course_id,
                )
            ]
        )

    monkeypatch.setattr(router_mod, "ingest_youtube", fake_ingest)

    resp = client.post(
        f"/courses/{subject.id}/ingest-youtube",
        json={"url": YT_URL, "subject_id": subject.id},
    )
    assert resp.status_code == 200
    resource_id = resp.json()["resource_id"]

    listing = client.get(f"/subjects/{subject.id}/resources")
    assert listing.status_code == 200
    assert resource_id in {r["id"] for r in listing.json()["resources"]}


def test_ingest_without_subject_id_behaves_as_before(
    client, wired, isolated_subject_store, monkeypatch
):
    """No subject_id -> the path course_id is used, nothing auto-attached."""
    router_mod = _inline_schedule(monkeypatch)
    captured = {}

    def fake_ingest(url, *, course_id, resource_id, **kwargs):
        captured["course_id"] = course_id
        wired.add(
            [
                make_chunk(
                    f"ch_{resource_id}",
                    resource_id=resource_id,
                    course_id=course_id,
                )
            ]
        )

    monkeypatch.setattr(router_mod, "ingest_youtube", fake_ingest)

    resp = client.post(
        "/courses/course_1/ingest-youtube", json={"url": YT_URL}
    )
    assert resp.status_code == 200
    assert captured["course_id"] == "course_1"
    subjects = isolated_subject_store.subject_store.list_all()
    assert all(not s.resource_ids for s in subjects)


# ---------------------------------------------------------------------------
# Upload title is the ORIGINAL filename, not the staged asset name
# ---------------------------------------------------------------------------

_UNSET_SENTINEL = object()


def _fake_ingest_recording_title(wired, monkeypatch):
    """Replace the router's ingest call, indexing chunks with the real title.

    The fake honours the ``title`` kwarg the router passes, so assertions
    cover the whole chain: multipart filename -> ``title=`` -> chunk metadata
    -> ``resources()``. No ASR, no network, nothing written to real data/.
    """
    import app.ingestion.router as router_mod
    from app.ingestion import jobs as jobs_mod_real
    from app.ingestion.chunking import TextSegment

    monkeypatch.setattr(router_mod, "schedule", lambda job, work: work())
    captured: dict = {}

    def fake_ingest(path, *, course_id, resource_id, **kwargs):
        captured["path"] = Path(path)
        captured["title"] = kwargs.get("title", _UNSET_SENTINEL)
        chunks = jobs_mod_real.build_chunks(
            resource_id=resource_id,
            course_id=course_id,
            source_type="video",
            text_segments=[TextSegment(text="hello", start_ms=0, end_ms=1_000)],
            title=(
                None
                if captured["title"] is _UNSET_SENTINEL
                else captured["title"]
            ),
        )
        wired.add(chunks)
        return {"total_chunks": len(chunks)}

    monkeypatch.setattr(router_mod, "ingest_media", fake_ingest)
    return captured


def _upload_and_read_resource(client, filename: str, payload: bytes = b"\x00" * 64):
    resp = client.post(
        "/courses/course_1/ingest",
        files={"file": (filename, payload, "video/mp4")},
    )
    assert resp.status_code == 200, resp.text
    resource_id = resp.json()["resource_id"]
    listing = client.get("/courses/course_1/resources").json()["resources"]
    return next(e for e in listing if e["id"] == resource_id)


def test_upload_title_is_original_filename(client, wired, monkeypatch):
    """The catalogue shows the name the learner uploaded.

    Regression: the upload path stages the file as ``{resource_id}.{ext}`` and
    the title came from that staged path, so every video was titled
    ``res_49ccbdb02150.mp4`` -- an internal id the learner never saw and
    cannot map back to their own file.
    """
    _fake_ingest_recording_title(wired, monkeypatch)
    entry = _upload_and_read_resource(client, "Partial-derivatives.mp4")
    assert entry["title"] == "Partial-derivatives.mp4"


def test_asset_name_remains_staged_name(client, wired, monkeypatch):
    """``asset_name`` is still the staged on-disk name.

    The media endpoint globs ``{resource_id}.*`` in the upload dir, so serving
    depends on the staged naming. ``title`` and ``asset_name`` are different
    fields for different purposes and must not be conflated.
    """
    captured = _fake_ingest_recording_title(wired, monkeypatch)
    entry = _upload_and_read_resource(client, "Partial-derivatives.mp4")

    assert entry["title"] == "Partial-derivatives.mp4"
    assert entry["asset_name"] == f"{entry['id']}.mp4"
    assert captured["path"].name == entry["asset_name"]
    assert captured["path"].exists()


def test_upload_filename_directory_is_stripped(client, wired, monkeypatch):
    """A browser can send a full local path; only the basename becomes a title.

    Unsanitised, that filename carries a separator and a drive letter into
    stored metadata and the UI, and a POSIX path could point anywhere.
    """
    _fake_ingest_recording_title(wired, monkeypatch)
    entry = _upload_and_read_resource(client, "C:\\Users\\x\\lecture.mp4")

    assert entry["title"] == "lecture.mp4"
    for separator in ("\\", "/"):
        assert separator not in entry["title"]
    assert "Users" not in entry["title"]


def test_missing_filename_leaves_title_null(wired, tmp_path, monkeypatch):
    """No usable filename -> ``title is None``, never a staged-name stand-in.

    ``{resource_id}.{ext}`` is a fabrication from the caller's point of view:
    it names an internal asset, not a file the learner chose. An honest empty
    state lets the UI fall back to the resource id.
    """
    from app.ingestion import jobs as jobs_mod_real
    from app.ingestion.jobs import sanitize_filename

    assert sanitize_filename(None) is None
    for junk in ("", "   ", "..", ".", "/", "///"):
        assert sanitize_filename(junk) is None

    fake = tmp_path / "staged.mp4"
    fake.write_bytes(b"not real media")
    monkeypatch.setattr(jobs_mod_real, "media_duration_ms", lambda path, **kw: 10_000)
    jobs_mod_real.ingest_media(
        fake, course_id="course_1", resource_id="res_noname",
        with_visual=False, title=None,
    )

    entry = next(e for e in wired.resources("course_1") if e["id"] == "res_noname")
    assert entry["title"] is None
    # Specifically not the staged file's own name.
    assert entry["title"] != fake.name


def test_title_is_length_capped(client, wired, monkeypatch):
    """An absurd filename is truncated before it reaches metadata."""
    from app.ingestion.jobs import MAX_TITLE_LENGTH, sanitize_filename

    # 900 chars: long enough to blow the 200-char cap, short enough that
    # python-multipart accepts the part header. A 5000-char name is rejected
    # with a 400 by the parser before the handler runs, which would test the
    # multipart parser rather than the cap.
    long_name = "a" * 896 + ".mp4"
    capped = sanitize_filename(long_name)
    assert len(capped) <= MAX_TITLE_LENGTH
    # The extension survives, so the UI can still tell what kind of file it is.
    assert capped.endswith(".mp4")

    _fake_ingest_recording_title(wired, monkeypatch)
    entry = _upload_and_read_resource(client, long_name)
    assert len(entry["title"]) <= MAX_TITLE_LENGTH
