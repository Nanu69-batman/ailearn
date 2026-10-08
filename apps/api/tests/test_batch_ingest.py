"""Backend-batch tests (offline: no key, no network).

Covers: YouTube URL validation (incl. shorts + playlist rejection), caption
VTT parsing, transcript JSON validation, ingest path-traversal rejection,
file/YouTube job lifecycles (fake embedder + fake transcribe/pipeline), and
registry singleton shared-visibility. Anything needing a live key or network
is explicitly skipped.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.chat.service import ChatService
from app.ingestion import router as ingest_router
from app.ingestion.embedder import HashingEmbedder
from app.ingestion.models import Chunk, Segment, text_hash
from app.ingestion.transcribe import (
    MAX_FIGURE_SEGMENT_S,
    MAX_SPEECH_SEGMENT_S,
    MAX_VISUAL_SEGMENT_S,
    MIN_FIGURE_SEGMENT_S,
    MIN_SPEECH_SEGMENT_S,
    MIN_VISUAL_SEGMENT_S,
    TRANSCRIBE_PROMPT,
    FigureSegment,
    VisualSegment,
    _to_figure_segments,
    _to_segments,
    _to_visual_segments,
    parse_transcribe_json,
    parse_transcript_json,
    validate_figure_payload,
    validate_transcript_payload,
)
from app.ingestion.youtube import (
    NormalizedMedia,
    VideoMetadata,
    extract_video_id,
    segments_from_caption_text,
)
from app.main import app
from app.retrieval.registry import get_shared_store, reset_shared_store
from app.retrieval.service import RetrievalService


class FakeEmbedder:
    """Tiny deterministic stand-in (offline; NOT a real embedding model)."""

    name = "fake-test-v1"
    dim = 8

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for i, tok in enumerate(text.lower().split()):
            vec[(hash(tok) % self.dim)] += 1.0 + (i % 3) * 0.5
        norm = sum(v * v for v in vec) ** 0.5 or 1.0
        return [v / norm for v in vec]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    reset_shared_store()
    ingest_router.reset_ingestion_state()
    monkeypatch.setattr(ingest_router, "TEST_DATA_DIR", tmp_path)
    monkeypatch.setattr(ingest_router, "get_embedder", lambda: FakeEmbedder())
    # Keep worker persistence (snapshot + ingest state) out of dev data/.
    from app.retrieval import registry

    monkeypatch.setattr(registry, "SNAPSHOT_PATH", tmp_path / "snap.pkl")
    monkeypatch.setattr(
        ingest_router, "INGEST_STATE_PATH", tmp_path / "ingest_state.json"
    )
    with TestClient(app) as c:
        yield c, tmp_path
    reset_shared_store()
    ingest_router.reset_ingestion_state()


# --- YouTube URL validation --------------------------------------------------


def test_youtube_accepts_watch_short_youtu_be():
    assert (
        extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    )
    assert extract_video_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert (
        extract_video_id("https://www.youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    )
    # Extra query params are fine when a video id is present.
    assert (
        extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s")
        == "dQw4w9WgXcQ"
    )


def test_youtube_rejects_playlists():
    with pytest.raises(ValueError, match="playlist"):
        extract_video_id("https://www.youtube.com/playlist?list=PLabc123XYZ")
    with pytest.raises(ValueError, match="playlist"):
        extract_video_id("https://www.youtube.com/watch?list=PLabc123XYZ")


def test_youtube_rejects_non_youtube():
    with pytest.raises(ValueError):
        extract_video_id("https://example.com/video/123")
    with pytest.raises(ValueError):
        extract_video_id("")
    with pytest.raises(ValueError):
        extract_video_id("https://www.youtube.com/watch")


# --- Caption VTT parsing -----------------------------------------------------


VTT_SAMPLE = """WEBVTT

00:00:00.000 --> 00:00:05.000
Hello and welcome back.

00:00:05.000 --> 00:00:11.500
Today we learn about retrieval.
"""


def test_caption_vtt_text_parse():
    segs = segments_from_caption_text(VTT_SAMPLE, "vid9")
    assert len(segs) == 2
    assert segs[0].start_ms == 0
    assert segs[0].end_ms == 5000
    assert segs[1].start_ms == 5000
    assert segs[1].end_ms == 11500
    assert all(s.video_id == "vid9" and s.source_type == "video" for s in segs)
    assert "retrieval" in segs[1].text


# --- Transcript JSON validation ----------------------------------------------


def _good_payload():
    return [
        {"start_s": 0.0, "end_s": 10.0, "text": "Welcome to the lesson."},
        {"start_s": 10.0, "end_s": 20.0, "text": "We cover embeddings today."},
        {"start_s": 20.0, "end_s": 30.0, "text": "Thanks for watching closely."},
    ]


def test_validate_transcript_payload_good():
    out = validate_transcript_payload(_good_payload(), duration_s=30.0)
    assert len(out) == 3
    assert out[0]["start_s"] == 0.0


def test_validate_transcript_payload_bad():
    good = _good_payload()
    bad_overlap = [dict(good[0]), {"start_s": 5.0, "end_s": 15.0, "text": "x" * 5}]
    with pytest.raises(ValueError):
        validate_transcript_payload(bad_overlap)
    bad_empty_text = [dict(good[0], text="   "), *good[1:]]
    with pytest.raises(ValueError):
        validate_transcript_payload(bad_empty_text)
    bad_beyond_duration = [dict(s) for s in good]
    bad_beyond_duration[-1]["end_s"] = 999.0
    with pytest.raises(ValueError):
        validate_transcript_payload(bad_beyond_duration, duration_s=30.0)
    bad_gap = [dict(good[0]), dict(good[2])]  # misses the tail of the media
    with pytest.raises(ValueError):
        validate_transcript_payload(bad_gap, duration_s=30.0)
    with pytest.raises(ValueError):
        validate_transcript_payload([])
    with pytest.raises(ValueError):
        parse_transcript_json("this is not json {{{")


def test_parse_transcript_json_strips_fences():
    raw = "```json\n" + __import__("json").dumps(_good_payload()) + "\n```"
    out = parse_transcript_json(raw, duration_s=30.0)
    assert len(out) == 3


# --- Visual (on-screen text) segments ---------------------------------------
#
# On-screen written content (board, tablet, slides) is captured as extra
# VisualSegments carrying their own start_ms/end_ms. They are NOT
# unconditionally in scope: they flow through the same strict end_ms <= T gate
# as speech, so a slide the instructor is still pointing at stays a spoiler.


def _visual_payload(on_screen):
    return __import__("json").dumps(
        {"speech": _good_payload(), "on_screen": on_screen}
    )


def test_visual_segments_get_start_and_end_ms():
    raw = _visual_payload(
        [
            {"start_s": 0.0, "end_s": 14.0, "on_screen_text": "f(x, y) = x^2 y + sin(y)"},
            {"start_s": 16.0, "end_s": 28.0, "on_screen_text": "df / dy (1, 2)"},
        ]
    )
    speech, visual_items, _figures = parse_transcribe_json(raw, duration_s=30.0)
    assert len(speech) == 3
    assert len(visual_items) == 2
    visual = _to_visual_segments(visual_items)
    assert len(visual) == 2
    for v in visual:
        # Non-null on both ends: a null end_ms is excluded by the defensive
        # filter in retrieval/store.py, so it must never be emitted.
        assert v.start_ms is not None
        assert v.end_ms is not None
        assert v.end_ms > v.start_ms
        assert v.source_type == "video"
    assert (visual[0].start_ms, visual[0].end_ms) == (0, 14_000)
    assert (visual[1].start_ms, visual[1].end_ms) == (16_000, 28_000)
    assert visual[0].text == "f(x, y) = x^2 y + sin(y)"


def test_visual_segments_without_end_ms_are_dropped():
    # Best-effort semantics: one unusable element must not fail the batch.
    raw = _visual_payload(
        [
            {"start_s": 0.0, "end_s": 14.0, "on_screen_text": "df / dx (1, 2)"},
            {"start_s": 16.0, "on_screen_text": "no end time"},
            {"start_s": 20.0, "end_s": 20.0, "on_screen_text": "zero length"},
            {"start_s": 40.0, "end_s": 900.0, "on_screen_text": "beyond duration"},
        ]
    )
    speech, visual_items, _figures = parse_transcribe_json(raw, duration_s=30.0)
    assert len(speech) == 3
    assert [v["on_screen_text"] for v in visual_items] == ["df / dx (1, 2)"]


def test_visual_speech_dedup():
    # The instructor reads the heading aloud while writing it: identical text in
    # the same window must not be indexed twice.
    payload = [
        {"start_s": 0.0, "end_s": 10.0, "text": "Partial derivatives introduction"},
        {"start_s": 10.0, "end_s": 20.0, "text": "We cover the chain rule today."},
        {"start_s": 20.0, "end_s": 30.0, "text": "Thanks for watching closely."},
    ]
    raw = __import__("json").dumps(
        {
            "speech": payload,
            "on_screen": [
                {
                    "start_s": 0.0,
                    "end_s": 10.0,
                    "on_screen_text": "Partial derivatives introduction",
                },
                {
                    "start_s": 10.0,
                    "end_s": 20.0,
                    "on_screen_text": "We cover the chain rule today.",
                },
                {
                    "start_s": 22.0,
                    "end_s": 28.0,
                    "on_screen_text": "df / dy (x, y) = x^2 + cos(y)",
                },
            ],
        }
    )
    speech, visual_items, _figures = parse_transcribe_json(raw, duration_s=30.0)
    assert len(speech) == 3
    # both read-aloud copies dropped; the equation never spoken verbatim kept
    assert [v["on_screen_text"] for v in visual_items] == [
        "df / dy (x, y) = x^2 + cos(y)"
    ]


def test_visual_dedup_does_not_span_windows():
    # Text that is WRITTEN in one window and read ALOUD much later is not a
    # duplicate: the speech copy cannot serve a learner paused at the moment
    # the writing appeared, so the visual copy must survive.
    # Speech must be contiguous (the speech contract), so pad the middle.
    filler = "Some narration in between that is nothing like the equation. "
    payload = [
        {"start_s": 0.0, "end_s": 10.0, "text": "Here is the formula on the board."},
        {"start_s": 10.0, "end_s": 20.0, "text": "We will come back to it later."},
    ]
    t = 20.0
    while t < 190.0:
        payload.append({"start_s": t, "end_s": t + 10.0, "text": filler})
        t += 10.0
    payload += [
        {"start_s": 190.0, "end_s": 200.0, "text": "partial f partial y"},
        {"start_s": 200.0, "end_s": 210.0, "text": "equals x squared plus cos y"},
    ]
    raw = __import__("json").dumps(
        {
            "speech": payload,
            "on_screen": [
                {
                    "start_s": 5.0,
                    "end_s": 15.0,
                    "on_screen_text": "partial f partial y equals x squared plus cos y",
                }
            ],
        }
    )
    _speech, visual_items, _figures = parse_transcribe_json(raw)
    assert [v["on_screen_text"] for v in visual_items] == [
        "partial f partial y equals x squared plus cos y"
    ]


def test_speech_output_unchanged():
    # The speech half of the response must be byte-compatible with the
    # audio-only contract: same fields, same values, same order, same indices.
    raw = _visual_payload(
        [
            {
                "start_s": 0.0,
                "end_s": 14.0,
                "on_screen_text": "f(x, y) = x^2 y + sin(y)",
            }
        ]
    )
    legacy = parse_transcript_json(
        "```json\n" + __import__("json").dumps(_good_payload()) + "\n```",
        duration_s=30.0,
    )
    speech, _visual, _figures = parse_transcribe_json(raw, duration_s=30.0)
    assert speech == legacy
    assert [s["text"] for s in speech] == [s["text"] for s in _good_payload()]
    assert all(set(s) == {"start_s", "end_s", "text"} for s in speech)
    segs = _to_segments(speech)
    assert [(s.source_type, s.index) for s in segs] == [
        ("video", 0),
        ("video", 1),
        ("video", 2),
    ]
    assert (segs[0].start_ms, segs[0].end_ms) == (0, 10_000)


def test_visual_chunks_pass_strict_t_gate():
    # End-to-end: a visual segment whose end_ms > T must be invisible, and the
    # same text with end_ms <= T must be retrievable. This is the property the
    # spoiler guarantee rests on.
    from app.ingestion.jobs import _chunk_segments
    from app.retrieval.store import InMemoryVectorStore

    T = 20_000
    row = {"id": "res1", "course_id": "c1", "type": "video", "video_id": "vid1"}
    segs = [
        Segment(text="Now the derivative.", source_type="video", start_ms=0, end_ms=10_000),
        VisualSegment(
            text="df / dy (x, y) = x^2 + cos(y)",
            source_type="video",
            start_ms=25_000,
            end_ms=40_000,  # still on screen -> spoiler at T
        ),
    ]
    chunks = _chunk_segments(segs, row, "hashing-v1")
    visual = [c for c in chunks if c.text.startswith("df / dy")]
    assert len(visual) == 1
    assert visual[0].start_ms == 25_000 and visual[0].end_ms == 40_000
    assert visual[0].source_type == "video"
    for c in chunks:
        assert c.start_ms is not None and c.end_ms is not None

    q = HashingEmbedder().embed("df dy x squared cos y")

    def _embed(cs):
        emb = HashingEmbedder().embed_batch([c.text for c in cs])
        for c, v in zip(cs, emb):
            c.embedding = v
        return cs

    hits = asyncio.run(
        InMemoryVectorStore(_embed(chunks)).search("c1", ["res1"], T, q, k=10)
    )
    assert not [h for h in hits if h.chunk.text.startswith("df / dy")]

    # Move the same text into the covered window: now it IS retrievable.
    segs[1].start_ms, segs[1].end_ms = 11_000, 19_000
    chunks2 = _chunk_segments(segs, row, "hashing-v1")
    visual2 = [c for c in chunks2 if c.text.startswith("df / dy")]
    assert len(visual2) == 1
    hits2 = asyncio.run(
        InMemoryVectorStore(_embed(chunks2)).search("c1", ["res1"], T, q, k=10)
    )
    assert [h.chunk.text for h in hits2 if h.chunk.text.startswith("df / dy")]


def test_figure_segments_pass_strict_t_gate():
    # End-to-end, and the property the spoiler guarantee rests on: a figure
    # whose end_ms > T must be invisible, and the SAME figure with end_ms <= T
    # must be retrievable. Figures are gated exactly like on-screen text, which
    # only holds because they keep source_type="video" and non-null timestamps.
    from app.ingestion.jobs import _chunk_segments
    from app.retrieval.store import InMemoryVectorStore

    T = 20_000
    row = {"id": "res1", "course_id": "c1", "type": "video", "video_id": "vid1"}
    segs = [
        Segment(text="Now the derivative.", source_type="video", start_ms=0, end_ms=10_000),
        FigureSegment(
            text="A yellow curve is drawn rising to the right",
            source_type="video",
            start_ms=25_000,
            end_ms=40_000,  # drawn after the learner paused -> spoiler at T
        ),
    ]
    chunks = _chunk_segments(segs, row, "hashing-v1")
    figs = [c for c in chunks if c.text.startswith("A yellow curve")]
    assert len(figs) == 1
    assert figs[0].start_ms == 25_000 and figs[0].end_ms == 40_000
    assert figs[0].source_type == "video"
    for c in chunks:
        assert c.start_ms is not None and c.end_ms is not None

    q = HashingEmbedder().embed("yellow curve drawn rising right")

    def _embed(cs):
        emb = HashingEmbedder().embed_batch([c.text for c in cs])
        for c, v in zip(cs, emb):
            c.embedding = v
        return cs

    hits = asyncio.run(
        InMemoryVectorStore(_embed(chunks)).search("c1", ["res1"], T, q, k=10)
    )
    assert not [h for h in hits if h.chunk.text.startswith("A yellow curve")]

    # Move the same figure into the covered window: now it IS retrievable.
    segs[1].start_ms, segs[1].end_ms = 11_000, 19_000
    chunks2 = _chunk_segments(segs, row, "hashing-v1")
    hits2 = asyncio.run(
        InMemoryVectorStore(_embed(chunks2)).search("c1", ["res1"], T, q, k=10)
    )
    assert [h.chunk.text for h in hits2 if h.chunk.text.startswith("A yellow curve")]


def test_figure_chunk_without_end_ms_is_not_emitted():
    from app.ingestion.jobs import _chunk_segments

    row = {"id": "res1", "course_id": "c1", "type": "video", "video_id": "vid1"}
    segs = [
        Segment(text="Now the derivative.", source_type="video", start_ms=0, end_ms=10_000),
        FigureSegment(text="orphan figure", source_type="video", start_ms=11_000, end_ms=None),
    ]
    chunks = _chunk_segments(segs, row, "hashing-v1")
    assert not [c for c in chunks if c.text == "orphan figure"]


def test_visual_chunk_without_end_ms_is_not_emitted():
    from app.ingestion.jobs import _chunk_segments

    row = {"id": "res1", "course_id": "c1", "type": "video", "video_id": "vid1"}
    segs = [
        Segment(text="Now the derivative.", source_type="video", start_ms=0, end_ms=10_000),
        VisualSegment(text="orphan visual", source_type="video", start_ms=11_000, end_ms=None),
    ]
    chunks = _chunk_segments(segs, row, "hashing-v1")
    assert not [c for c in chunks if c.text == "orphan visual"]


def test_visual_chunk_dropped_when_already_indexed_as_speech():
    # Defence in depth: even if dedup at parse time is bypassed, the chunker
    # refuses to index the same text twice for one resource.
    from app.ingestion.jobs import _chunk_segments

    row = {"id": "res1", "course_id": "c1", "type": "video", "video_id": "vid1"}
    segs = [
        Segment(text="df / dy (x, y) = x^2 + cos(y)", source_type="video", start_ms=0, end_ms=10_000),
        VisualSegment(
            text="df / dy (x, y) = x^2 + cos(y)",
            source_type="video",
            start_ms=11_000,
            end_ms=19_000,
        ),
    ]
    chunks = _chunk_segments(segs, row, "hashing-v1")
    assert len([c for c in chunks if c.text.startswith("df / dy")]) == 1


def test_transcript_cache_key_versioned(tmp_path: Path):
    # A v1 (audio-only) entry must not be replayed by the visual-aware path.
    from app.ingestion.transcribe import TRANSCRIPT_CACHE_VERSION, transcript_cache_key

    media = tmp_path / "lecture.mp4"
    media.write_bytes(b"not a real video; only its hash is used here")
    key = transcript_cache_key(media)
    assert key.startswith("transcript:" + TRANSCRIPT_CACHE_VERSION + ":")
    assert "gemini-3.5-flash-lite" in key
    legacy_key = key.replace("transcript:" + TRANSCRIPT_CACHE_VERSION + ":", "transcript:")
    assert legacy_key != key


# --- Duration bounds: on-screen vs speech -------------------------------------
#
# Regression guards for the ingest failure where Gemini's legitimate 56.9s
# on-screen equation was rejected by the speech bound and the job died. The
# bounds are now named constants shared by the prompt text and the validators.


def _speech_covering(until_s: float):
    """Valid, contiguous speech filling 0..until_s, every element <=45s."""
    speech = []
    start = 0.0
    while start < until_s:
        end = min(start + 40.0, until_s)
        speech.append(
            {"start_s": start, "end_s": end, "text": f"spoken beat at {start:.0f}s"}
        )
        start = end
    return speech


def test_on_screen_segment_longer_than_speech_max_is_accepted():
    # 57s and 90s of on-screen writing both exceed MAX_SPEECH_SEGMENT_S and
    # must survive, since a derivation stays on the board far longer than a
    # spoken sentence. Speech itself stays inside the unchanged bounds.
    assert MAX_VISUAL_SEGMENT_S > MAX_SPEECH_SEGMENT_S
    raw = __import__("json").dumps(
        {
            "speech": _speech_covering(160.0),
            "on_screen": [
                {
                    "start_s": 0.0,
                    "end_s": 57.0,
                    "on_screen_text": "f(x, y) = x^2 y + sin(y)   [written on board]",
                },
                {
                    "start_s": 60.0,
                    "end_s": 150.0,
                    "on_screen_text": "df / dy (x, y) = x^2 + cos(y)   [full derivation]",
                },
            ],
        }
    )
    _speech, visual_items, _figures = parse_transcribe_json(raw, duration_s=160.0)
    assert [v["on_screen_text"] for v in visual_items] == [
        "f(x, y) = x^2 y + sin(y)   [written on board]",
        "df / dy (x, y) = x^2 + cos(y)   [full derivation]",
    ]
    segments = _to_visual_segments(visual_items)
    assert [(s.start_ms, s.end_ms) for s in segments] == [
        (0, 57_000),
        (60_000, 150_000),
    ]
    # Still garbage-tolerant: beyond the on-screen ceiling is dropped.
    over = __import__("json").dumps(
        {
            "speech": _speech_covering(400.0),
            "on_screen": [
                {"start_s": 0.0, "end_s": 400.0, "on_screen_text": "whole lecture"}
            ],
        }
    )
    assert parse_transcribe_json(over, duration_s=400.0)[1] == []


def test_speech_segment_outside_bounds_still_rejected():
    # Guards against the fix being implemented by loosening speech instead.
    good = _good_payload()
    long_speech = [*good, {"start_s": 30.0, "end_s": 87.0, "text": "one long segment"}]
    with pytest.raises(ValueError, match="outside"):
        validate_transcript_payload(long_speech, duration_s=90.0)
    short_speech = [*good, {"start_s": 30.0, "end_s": 30.5, "text": "blip"}]
    with pytest.raises(ValueError, match="outside"):
        validate_transcript_payload(short_speech, duration_s=90.0)
    # The speech ceiling is untouched at its documented value.
    assert MAX_SPEECH_SEGMENT_S == 45.0
    assert MIN_SPEECH_SEGMENT_S == 2.0


def test_prompt_and_validator_share_duration_constant():
    # The bug was drift: the prompt stated one rule for both arrays while the
    # validator enforced another. Assert the rendered prompt actually carries
    # the numbers the validators enforce, for both arrays, and that the two
    # arrays differ.
    speech_max = f"{MAX_SPEECH_SEGMENT_S:g}"
    visual_max = f"{MAX_VISUAL_SEGMENT_S:g}"
    speech_min = f"{MIN_SPEECH_SEGMENT_S:g}"
    visual_min = f"{MIN_VISUAL_SEGMENT_S:g}"
    assert f"{speech_min}-{speech_max} seconds" in TRANSCRIBE_PROMPT
    assert f"{visual_min}-{visual_max} seconds" in TRANSCRIBE_PROMPT
    assert MAX_VISUAL_SEGMENT_S != MAX_SPEECH_SEGMENT_S
    # The prompt must not restate a stale single shared range for both arrays.
    assert "5-15 seconds each" not in TRANSCRIBE_PROMPT


def test_on_screen_without_end_ms_is_dropped():
    # Unchanged behaviour, pinned: no usable end time -> dropped, never
    # defaulted, so it can never slip past the strict end_ms <= T gate.
    assert _to_visual_segments([{"start_s": 5.0, "end_s": 40.0, "on_screen_text": ""}]) == []
    visual = _to_visual_segments(
        [{"start_s": 5.0, "end_s": 40.0, "on_screen_text": "df / dy = x^2"}]
    )
    assert len(visual) == 1
    assert visual[0].start_ms is not None
    assert visual[0].end_ms is not None


# --- Figure (appearance / gesture) segments ---------------------------------
#
# A learner can only ask about what was SEEN. "What is that yellow function",
# "what does the arrow curve represent" are unanswerable unless the words colour,
# arrow and curve were ingested, so figure narration is captured as FigureSegments
# alongside on-screen text -- and, like it, gated on end_ms <= T.


def _figure_payload(figures, on_screen=None):
    return __import__("json").dumps(
        {
            "speech": _good_payload(),
            "on_screen": on_screen or [],
            "figures": figures,
        }
    )


def test_figure_segments_get_start_and_end_ms():
    raw = _figure_payload(
        [
            {
                "start_s": 0.0,
                "end_s": 14.0,
                "figure_text": "A yellow curve is drawn rising to the right",
            },
            {
                "start_s": 16.0,
                "end_s": 28.0,
                "figure_text": "He draws a blue arrow from x towards y",
            },
        ]
    )
    speech, visual_items, figure_items = parse_transcribe_json(raw, duration_s=30.0)
    assert len(speech) == 3
    assert visual_items == []
    assert len(figure_items) == 2
    figures = _to_figure_segments(figure_items)
    assert len(figures) == 2
    for f in figures:
        # Non-null on both ends, exactly like on-screen text: a null end_ms is
        # excluded by the defensive filter in retrieval/store.py, so a figure
        # that could be a spoiler must never be emitted without a real end.
        assert f.start_ms is not None
        assert f.end_ms is not None
        assert f.end_ms > f.start_ms
        assert f.source_type == "video"
    assert (figures[0].start_ms, figures[0].end_ms) == (0, 14_000)
    assert (figures[1].start_ms, figures[1].end_ms) == (16_000, 28_000)
    assert figures[0].text == "A yellow curve is drawn rising to the right"


def test_figure_without_end_ms_is_dropped():
    # Best-effort semantics, identical to on-screen text: an element with no
    # usable end time is DROPPED, never defaulted to null/0, so it cannot slip
    # past the strict end_ms <= T gate. One bad element must not fail the batch.
    raw = _figure_payload(
        [
            {"start_s": 0.0, "end_s": 14.0, "figure_text": "A green arrow points up"},
            {"start_s": 16.0, "figure_text": "no end time"},
            {"start_s": 20.0, "end_s": 20.0, "figure_text": "zero length"},
            {"start_s": 40.0, "end_s": 900.0, "figure_text": "beyond duration"},
            {"start_s": 24.0, "end_s": 26.0, "figure_text": "   "},
        ]
    )
    speech, _visual, figure_items = parse_transcribe_json(raw, duration_s=30.0)
    assert len(speech) == 3
    assert [f["figure_text"] for f in figure_items] == ["A green arrow points up"]
    # Same at the segment-conversion layer, which must also refuse a null end.
    assert _to_figure_segments([{"start_s": 5.0, "figure_text": "x"}]) == []
    kept = _to_figure_segments(
        [{"start_s": 5.0, "end_s": 40.0, "figure_text": "a blue curve"}]
    )
    assert len(kept) == 1
    assert kept[0].start_ms == 5_000 and kept[0].end_ms == 40_000


def test_figures_dedupe_against_speech_and_on_screen():
    # A figure that is only a restatement of what was already said aloud, or of
    # writing already captured, carries no new information and would
    # double-weight that content in the embedding space.
    payload = [
        {"start_s": 0.0, "end_s": 10.0, "text": "He draws an arrow from x to y."},
        {"start_s": 10.0, "end_s": 20.0, "text": "That is the curve we discussed."},
        {"start_s": 20.0, "end_s": 30.0, "text": "Thanks for watching closely."},
    ]
    raw = __import__("json").dumps(
        {
            "speech": payload,
            "on_screen": [
                {"start_s": 10.0, "end_s": 20.0, "on_screen_text": "df / dy (x, y)"}
            ],
            "figures": [
                # dropped: narrated aloud in the same window
                {
                    "start_s": 2.0,
                    "end_s": 9.0,
                    "figure_text": "He draws an arrow from x to y",
                },
                # dropped: restates the on-screen text in the same window
                {
                    "start_s": 12.0,
                    "end_s": 18.0,
                    "figure_text": "df / dy (x, y)",
                },
                # kept: genuinely visual, only seen
                {
                    "start_s": 22.0,
                    "end_s": 29.0,
                    "figure_text": "A yellow curve is drawn rising to the right",
                },
            ],
        }
    )
    speech, _visual, figure_items = parse_transcribe_json(raw, duration_s=30.0)
    assert len(speech) == 3
    assert [f["figure_text"] for f in figure_items] == [
        "A yellow curve is drawn rising to the right"
    ]


def test_figure_dedup_does_not_span_windows():
    # Same rule as on-screen text: a curve DRAWN at 5s must not be considered
    # narrated by speech at 195s, because the speech copy cannot serve a learner
    # paused at the moment the curve appeared.
    filler = "Some narration in between that mentions nothing visual at all. "
    payload = [
        {"start_s": 0.0, "end_s": 10.0, "text": "Here is the picture on the board."},
        {"start_s": 10.0, "end_s": 20.0, "text": "We will come back to it later."},
    ]
    t = 20.0
    while t < 190.0:
        payload.append({"start_s": t, "end_s": t + 10.0, "text": filler})
        t += 10.0
    payload += [
        {"start_s": 190.0, "end_s": 200.0, "text": "a yellow curve"},
        {"start_s": 200.0, "end_s": 210.0, "text": "rising to the right"},
    ]
    raw = __import__("json").dumps(
        {
            "speech": payload,
            "on_screen": [],
            "figures": [
                {
                    "start_s": 5.0,
                    "end_s": 15.0,
                    "figure_text": "a yellow curve rising to the right",
                }
            ],
        }
    )
    _speech, _visual, figure_items = parse_transcribe_json(raw)
    assert [f["figure_text"] for f in figure_items] == [
        "a yellow curve rising to the right"
    ]


def test_prompt_and_validator_share_figure_constants():
    # Same drift guard as the speech/visual pair, extended to the figures array:
    # the rendered prompt must carry exactly the numbers the figures validator
    # enforces, and the raw template must not be what the validator reads.
    assert MIN_FIGURE_SEGMENT_S == 1.0
    assert MAX_FIGURE_SEGMENT_S == 120.0
    fig_range = (
        f"{MIN_FIGURE_SEGMENT_S:g}-{MAX_FIGURE_SEGMENT_S:g} seconds"
    )
    assert fig_range in TRANSCRIBE_PROMPT
    # The prompt must name the figures array and forbid guessing.
    assert '"figures"' in TRANSCRIBE_PROMPT
    assert "omit that element entirely rather than guessing" in TRANSCRIBE_PROMPT
    # The validator must reject on exactly those bounds, not on speech's.
    assert MAX_FIGURE_SEGMENT_S == MAX_VISUAL_SEGMENT_S
    assert MAX_FIGURE_SEGMENT_S != MAX_SPEECH_SEGMENT_S
    # A figure longer than the figures ceiling (but far shorter than the old
    # lecture-sized allowance) is dropped; one inside the ceiling survives.
    long_fig = [
        {"start_s": 0.0, "end_s": 130.0, "figure_text": "diagram still on screen"}
    ]
    assert validate_figure_payload(long_fig, duration_s=200.0) == []
    ok_fig = [{"start_s": 0.0, "end_s": 100.0, "figure_text": "a yellow curve"}]
    assert len(validate_figure_payload(ok_fig, duration_s=200.0)) == 1


# --- Speech notation normalisation + visual fragment merging -------------------
#
# A maths lecturer SAYS the equation ("x squared y plus sine of y") and WRITES it
# ("= f(x, y)", "= x^2 y", "= + sin(y)"). Both halves used to be unmatchable:
# the speech was prose no learner would type, the writing was fragmented into
# unembeddable stubs. Prompt guidance fixes the first, fragment merging fixes the
# second. Neither may move a timestamp -- the strict end_ms <= T gate is the
# only thing preventing a spoiler.


def test_speech_notation_normalisation_rules_are_in_prompt():
    # Few-shot guidance, not a regex post-processor: the rule and its examples
    # are named constants substituted into the prompt, so instruction and
    # examples cannot drift apart.
    from app.ingestion.transcribe import (
        SPEECH_NOTATION_EXAMPLES,
        SPEECH_NOTATION_RULE,
    )

    # The rule and every example are actually rendered into the live prompt.
    assert SPEECH_NOTATION_RULE in TRANSCRIBE_PROMPT
    assert SPEECH_NOTATION_EXAMPLES in TRANSCRIBE_PROMPT
    # The four target normalisations, verbatim.
    for example in (
        'x squared y plus sine of y -> "x^2 y + sin(y)"',
        'the partial of f with respect to y -> "partial f / partial y"',
        'd y d x -> "dy/dx"',
        'one plus cosine of two -> "1 + cos(2)"',
    ):
        assert example in TRANSCRIBE_PROMPT
    # The no-invention guard: notation only, never content the speaker did not say.
    assert "never add, infer or complete content that was not said" in TRANSCRIBE_PROMPT
    assert "keeping the instructor's own wording" in TRANSCRIBE_PROMPT
    # Placeholders must be fully substituted -- a literal @SPEECH_NOTATION@ would
    # mean the guidance silently never reached the model.
    assert "@SPEECH_NOTATION" not in TRANSCRIBE_PROMPT


def test_speech_segments_keep_timestamps():
    # Normalising notation is a TEXT-only transform. Timestamps, ordering,
    # contiguity, coverage and the duration bounds are the speech contract and
    # must be byte-identical whatever notation the model chose to emit.
    payload = [
        {
            "start_s": 0.0,
            "end_s": 10.0,
            "text": "f(x, y) = x^2 y + sin(y)",
        },
        {
            "start_s": 10.0,
            "end_s": 20.0,
            "text": "the partial of f with respect to y is x^2 + cos(y)",
        },
        {"start_s": 20.0, "end_s": 30.0, "text": "d y d x, and dy/dx is the answer"},
    ]
    speech, _visual, _figures = parse_transcribe_json(
        __import__("json").dumps({"speech": payload, "on_screen": []}),
        duration_s=30.0,
    )
    assert [(s["start_s"], s["end_s"]) for s in speech] == [
        (0.0, 10.0),
        (10.0, 20.0),
        (20.0, 30.0),
    ]
    segs = _to_segments(speech)
    assert [(s.start_ms, s.end_ms) for s in segs] == [(0, 10_000), (10_000, 20_000), (20_000, 30_000)]
    assert [s.text for s in segs] == [p["text"] for p in payload]
    assert [s.index for s in segs] == [0, 1, 2]


def _vseg(text: str, start_ms: int, end_ms: int) -> VisualSegment:
    return VisualSegment(
        text=text, source_type="video", start_ms=start_ms, end_ms=end_ms
    )


def test_merged_visual_chunks_cover_full_span():
    # Three same-kind fragments written seconds apart become ONE chunk whose
    # start is the min start and whose end is the max end of the parts merged.
    from app.ingestion.jobs import _chunk_segments

    row = {"id": "res1", "course_id": "c1", "type": "video", "video_id": "vid1"}
    segs = [
        Segment(text="Now the function.", source_type="video", start_ms=0, end_ms=10_000),
        _vseg("= f(x, y)", 12_000, 16_000),
        _vseg("= x^2 y", 18_000, 22_000),
        _vseg("= + sin(y)", 24_000, 28_000),
    ]
    chunks = _chunk_segments(segs, row, "hashing-v1")
    visual = [c for c in chunks if "f(x, y)" in c.text]
    assert len(visual) == 1
    # min start / max end across the merged parts.
    assert visual[0].start_ms == 12_000
    assert visual[0].end_ms == 28_000
    # All three fragments survive in the merged text.
    for fragment in ("= f(x, y)", "= x^2 y", "= + sin(y)"):
        assert fragment in visual[0].text
    for c in chunks:
        assert c.start_ms is not None and c.end_ms is not None


def test_merge_does_not_extend_end_ms_beyond_content():
    # The spoiler guard. A merged chunk's end_ms is the MAXIMUM of the merged
    # parts' own end times -- never the merge window, the span cap, or the next
    # fragment's start. Fragments far apart stay separate, so no chunk can claim
    # content the learner has not reached.
    from app.ingestion.jobs import (
        VISUAL_MERGE_MAX_GAP_MS,
        _chunk_segments,
    )

    row = {"id": "res1", "course_id": "c1", "type": "video", "video_id": "vid1"}
    segs = [
        Segment(text="Now the function.", source_type="video", start_ms=0, end_ms=10_000),
        _vseg("= f(x, y)", 12_000, 16_000),
        # Far beyond the merge gap: must NOT be pulled into the first chunk, and
        # must not push its end_ms out.
        _vseg("= + sin(y)", 16_000 + VISUAL_MERGE_MAX_GAP_MS + 5_000, 40_000),
    ]
    chunks = _chunk_segments(segs, row, "hashing-v1")
    first = [c for c in chunks if "f(x, y)" in c.text]
    second = [c for c in chunks if "+ sin(y)" in c.text]
    assert len(first) == 1 and len(second) == 1
    # end_ms is the last OBSERVED writing, not the far fragment's start/end.
    assert first[0].end_ms == 16_000
    assert first[0].end_ms < 16_000 + VISUAL_MERGE_MAX_GAP_MS
    # And the far fragment keeps its own true end_ms.
    assert second[0].start_ms == 16_000 + VISUAL_MERGE_MAX_GAP_MS + 5_000
    assert second[0].end_ms == 40_000

    # A tight gap does merge, and still stops at the real max end.
    tight = [
        Segment(text="Now the function.", source_type="video", start_ms=0, end_ms=10_000),
        _vseg("= df / dy", 12_000, 15_000),
        _vseg("= 2xy + cos(y)", 16_000, 19_000),
    ]
    merged = [c for c in _chunk_segments(tight, row, "hashing-v1") if "df / dy" in c.text]
    assert len(merged) == 1
    assert (merged[0].start_ms, merged[0].end_ms) == (12_000, 19_000)


def test_short_visual_fragments_are_not_orphaned():
    # End-to-end: the burst of tiny fragments one equation produces must be
    # retrievable as ONE chunk covering them, and only when T has passed the
    # last fragment.
    from app.ingestion.jobs import _chunk_segments
    from app.retrieval.store import InMemoryVectorStore

    row = {"id": "res1", "course_id": "c1", "type": "video", "video_id": "vid1"}
    fragments = [
        _vseg("= f(x, y)", 2_000, 5_000),
        _vseg("= x^2 y", 6_000, 9_000),
        _vseg("= + sin(y)", 10_000, 13_000),
    ]
    segs = [
        Segment(text="Let us define it.", source_type="video", start_ms=0, end_ms=10_000),
        *fragments,
    ]

    def _embed(cs):
        emb = HashingEmbedder().embed_batch([c.text for c in cs])
        for c, v in zip(cs, emb):
            c.embedding = v
        return cs

    q = HashingEmbedder().embed("what is the f(x,y) function")

    # T before the burst finished: nothing from the equation is retrievable.
    chunks = _chunk_segments(segs, row, "hashing-v1")
    hits = asyncio.run(
        InMemoryVectorStore(_embed(chunks)).search("c1", ["res1"], 6_000, q, k=10)
    )
    assert not [h for h in hits if "f(x, y)" in h.chunk.text]

    # T past the burst: one chunk covering all three fragments is retrievable.
    chunks2 = _chunk_segments(segs, row, "hashing-v1")
    eq = [c for c in chunks2 if "f(x, y)" in c.text]
    assert len(eq) == 1
    hits2 = asyncio.run(
        InMemoryVectorStore(_embed(chunks2)).search("c1", ["res1"], 13_000, q, k=10)
    )
    got = [h.chunk for h in hits2 if "f(x, y)" in h.chunk.text]
    assert len(got) == 1
    assert (got[0].start_ms, got[0].end_ms) == (2_000, 13_000)


# --- Speech timestamp fidelity -------------------------------------------------
#
# A citation must point at the moment the words were spoken, not at the outer
# edge of a merged window. The demo clip exposed this: a line Gemini places at
# 287.0s was indexed inside a chunk starting at 258.9s, so the citation read
# ~28s early. These tests pin the two properties that fix that, plus the
# end_ms bound that makes the tightening safe (a spoiler must never widen).


def test_speech_chunk_end_ms_bounds_its_own_content():
    # A chunk's end_ms is the last merged segment's own end time -- never the
    # window edge, the overlap, or the next segment's start. This is the spoiler
    # guard: widening end_ms past real content would let a learner at time T
    # receive words said after T.
    from app.ingestion.jobs import _chunk_segments

    row = {"id": "res1", "course_id": "c1", "type": "video", "video_id": "vid1"}
    segs = [
        Segment(text=f"Sentence{i} is finished here.", source_type="video",
                start_ms=i * 4_000, end_ms=i * 4_000 + 3_800, index=i)
        for i in range(30)
    ]
    # Real end time per segment, keyed by its unique token.
    seg_end_by_token = {f"Sentence{i}": s.end_ms for i, s in enumerate(segs)}

    chunks = _chunk_segments(segs, row, "hashing-v1")
    assert chunks
    for c in chunks:
        assert c.end_ms is not None and c.start_ms is not None
        # The last segment this chunk actually contains, by its own token.
        last = [t for t in c.text.split() if t.startswith("Sentence")][-1]
        # The chunk cannot end after that segment's own end time...
        assert c.end_ms <= seg_end_by_token[last]
        # ...and the bound is tight rather than merely satisfied: an end_ms
        # earlier than real content would understate how long the learner must
        # wait before this passage is legitimately theirs.
        assert c.end_ms == seg_end_by_token[last]


def test_speech_chunks_do_not_span_large_time_gaps():
    # A window must never bridge a long pause: doing so gives the chunk a
    # start_ms from before the pause, so anything said after it inherits a start
    # far earlier than it was spoken.
    from app.ingestion.jobs import SPEECH_MERGE_MAX_GAP_MS, _chunk_segments

    row = {"id": "res1", "course_id": "c1", "type": "video", "video_id": "vid1"}
    pre_gap_end = 40_000
    post_gap_start = pre_gap_end + SPEECH_MERGE_MAX_GAP_MS + 60_000
    segs = [
        Segment(text=f"Before the pause {i} is done.", source_type="video",
                start_ms=i * 4_000, end_ms=i * 4_000 + 3_800, index=i)
        for i in range(10)
    ] + [
        Segment(text=f"After the pause {i} is done.", source_type="video",
                start_ms=post_gap_start + i * 4_000,
                end_ms=post_gap_start + i * 4_000 + 3_800, index=10 + i)
        for i in range(10)
    ]
    chunks = _chunk_segments(segs, row, "hashing-v1")

    before = [c for c in chunks if "Before the pause" in c.text]
    after = [c for c in chunks if "After the pause" in c.text]
    assert before and after
    # No chunk mixes content from both sides of the pause.
    assert not [c for c in chunks if "Before" in c.text and "After" in c.text]
    # Nothing before the pause reaches past its own end.
    for c in before:
        assert c.end_ms <= pre_gap_end
    # Nothing after the pause is stamped from before it.
    for c in after:
        assert c.start_ms >= post_gap_start


def test_visual_chunks_keep_exact_intervals():
    # No regression to the visual/figure path: on-screen text keeps its own
    # observed interval, because that end_ms is what the strict end_ms <= T
    # gate tests. Tightening the speech windows must not smear these.
    from app.ingestion.jobs import _chunk_segments
    from app.ingestion.transcribe import FigureSegment, VisualSegment

    row = {"id": "res1", "course_id": "c1", "type": "video", "video_id": "vid1"}
    segs = [
        Segment(text="Speech that is being said right now.", source_type="video",
                start_ms=0, end_ms=3_000, index=0),
        VisualSegment(text="= f(x, y)", source_type="video",
                      start_ms=7_000, end_ms=11_000, index=1),
        FigureSegment(text="A yellow curve", source_type="video",
                      start_ms=19_000, end_ms=24_500, index=2),
    ]
    chunks = _chunk_segments(segs, row, "hashing-v1")

    vis = [c for c in chunks if c.text == "= f(x, y)"]
    fig = [c for c in chunks if c.text == "A yellow curve"]
    assert len(vis) == 1 and len(fig) == 1
    # Exactly the observed interval -- neither widened by a speech window nor
    # pulled towards the speech chunk's own bounds.
    assert (vis[0].start_ms, vis[0].end_ms) == (7_000, 11_000)
    assert (fig[0].start_ms, fig[0].end_ms) == (19_000, 24_500)
    assert all(c.source_type == "video" for c in chunks)
    # And the strict-T gate still hides the figure before it was drawn.
    for c in (vis[0], fig[0]):
        assert c.end_ms is not None


# --- Ingest path safety ------------------------------------------------------


def test_ingest_rejects_traversal_and_absolute(client):
    c, _tmp = client
    r = c.post("/courses/demo/ingest", json={"path": "../evil.txt"})
    assert r.status_code == 422
    r = c.post("/courses/demo/ingest", json={"path": "sub/../../evil.txt"})
    assert r.status_code == 422
    r = c.post("/courses/demo/ingest", json={"path": "C:/Windows/secret.txt"})
    assert r.status_code == 422
    r = c.post("/courses/demo/ingest", json={"path": "missing.txt"})
    assert r.status_code == 404


# --- Job lifecycles ----------------------------------------------------------


def _vid_segments() -> list[Segment]:
    return [
        Segment(
            text=f"Lesson point number {i} explained clearly here.",
            source_type="video",
            start_ms=i * 10000,
            end_ms=(i + 1) * 10000,
        )
        for i in range(6)
    ]


def test_txt_ingest_job_lifecycle_and_retrieval(client):
    c, tmp = client
    (tmp / "notes.txt").write_text(
        "Photosynthesis converts light.\n\nChlorophyll absorbs photons daily.",
        encoding="utf-8",
    )
    r = c.post("/courses/demo/ingest", json={"path": "notes.txt"})
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "pending" and body["job_id"] and body["resource_id"]

    job = c.get(f"/ingestion/jobs/{body['job_id']}").json()
    assert job["status"] == "done", job
    assert job["error"] is None

    res = c.get("/courses/demo/resources").json()
    assert res["course_id"] == "demo"  # auto-created course
    assert len(res["resources"]) == 1
    entry = res["resources"][0]
    assert entry["provider"] == "file"
    assert entry["youtube_id"] is None
    assert entry["title"] == "notes.txt"
    assert entry["status"] == "done"

    svc = RetrievalService(store=get_shared_store(), embedder=FakeEmbedder())
    passages = asyncio.run(
        svc.retrieve("photosynthesis light", "demo", [entry["id"]], 60_000, k=5)
    )
    assert passages and all(p.chunk_id for p in passages)


def test_mp4_ingest_uses_transcribe_fake(client, monkeypatch):
    c, tmp = client
    (tmp / "clip.mp4").write_bytes(b"\x00\x00fake-mp4")
    monkeypatch.setattr(
        ingest_router, "transcribe_with_gemini", lambda *a, **k: _vid_segments()
    )
    r = c.post("/courses/demo/ingest", json={"path": "clip.mp4"})
    assert r.status_code == 202, r.text
    job = c.get(f"/ingestion/jobs/{r.json()['job_id']}").json()
    assert job["status"] == "done", job
    res = c.get("/courses/demo/resources").json()
    assert res["resources"][0]["status"] == "done"


def test_youtube_ingest_job_lifecycle_fakes(client, monkeypatch):
    c, _tmp = client
    monkeypatch.setattr(
        ingest_router,
        "fetch_metadata",
        lambda url: VideoMetadata(
            video_id="dQw4w9WgXcQ", title="Demo vid", duration_s=120.0
        ),
    )
    monkeypatch.setattr(
        ingest_router,
        "ingest_youtube",
        lambda url: NormalizedMedia(
            video_id="dQw4w9WgXcQ", title="Demo vid", segments=_vid_segments()
        ),
    )
    r = c.post(
        "/courses/demo/ingest-youtube",
        json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
    )
    assert r.status_code == 202, r.text
    job = c.get(f"/ingestion/jobs/{r.json()['job_id']}").json()
    assert job["status"] == "done", job
    res = c.get("/courses/demo/resources").json()
    entry = res["resources"][0]
    assert entry["provider"] == "youtube"
    assert entry["youtube_id"] == "dQw4w9WgXcQ"
    assert entry["title"] == "Demo vid"
    assert entry["status"] == "done"


def test_youtube_rejects_bad_url_and_long_video(client, monkeypatch):
    c, _tmp = client
    r = c.post(
        "/courses/demo/ingest-youtube",
        json={"url": "https://www.youtube.com/playlist?list=PLabc123XYZ"},
    )
    assert r.status_code == 422
    monkeypatch.setattr(
        ingest_router,
        "fetch_metadata",
        lambda url: VideoMetadata(
            video_id="dQw4w9WgXcQ", title="Too long", duration_s=46 * 60.0
        ),
    )
    r = c.post(
        "/courses/demo/ingest-youtube",
        json={"url": "https://youtu.be/dQw4w9WgXcQ"},
    )
    assert r.status_code == 422


def test_unknown_job_404(client):
    c, _tmp = client
    assert c.get("/ingestion/jobs/nope").status_code == 404


# --- Registry singleton shared-visibility ------------------------------------


def test_registry_singleton_shared_with_chat_default():
    from app.ingestion.embedder import HashingEmbedder

    reset_shared_store()
    store = get_shared_store()
    assert get_shared_store() is store
    emb = HashingEmbedder()
    text = "mitochondria produce cellular energy stores"
    chunk = Chunk(
        id="reg1",
        resource_id="r1",
        course_id="demo",
        source_type="pdf",
        chunk_index=0,
        text=text,
        text_hash=text_hash(text),
        embed_model=emb.name,
        page_no=1,
        embedding=emb.embed(text),
    )
    store.index([chunk])
    # Pin the embedder: the chunk above is embedded with HashingEmbedder, so
    # retrieval must use the same provider. Leaving it to the ambient default
    # made this test depend on whether EMBEDDING_API_KEY happens to be set.
    svc = ChatService(store=store, embedder=emb)  # default store -> shared registry
    assert svc._store is store
    passages = asyncio.run(
        svc._retrieval().retrieve("cellular energy", "demo", ["r1"], 60_000, k=5)
    )
    assert [p.chunk_id for p in passages] == ["reg1"]
    reset_shared_store()


def test_media_endpoint_streams_the_ingested_file(client):
    """Regression: every file resource must serve ITS OWN asset.

    Previously the player used one shared env URL, so Watch on any file showed
    the same demo video. The media endpoint is keyed by resource id.
    """
    c, tmp = client
    (tmp / "notes.txt").write_text("hello", encoding="utf-8")
    job = c.post("/courses/demo/ingest", json={"path": "notes.txt"})
    rid = job.json()["resource_id"]
    # Let the background worker run synchronously (TestClient runs tasks inline)
    for _ in range(50):
        if c.get(f"/ingestion/jobs/{job.json()['job_id']}").json()["status"] == "done":
            break
    res = c.get(f"/resources/{rid}/media")
    assert res.status_code == 200
    assert res.text == "hello"
    # An unknown resource 404s instead of serving another asset.
    assert c.get("/resources/does-not-exist/media").status_code == 404


def test_rebuilt_resource_without_asset_has_no_media(client):
    """Snapshot-rebuilt entries must 404 on media, never borrow another file."""
    c, _ = client
    rid = "orphan1"
    ingest_router._resources[rid] = {
        "id": rid,
        "course_id": "demo",
        "type": "video",
        "provider": "file",
        "youtube_id": None,
        "title": rid,
        "status": "done",
        "asset_name": None,
    }
    assert c.get(f"/resources/{rid}/media").status_code == 404


def test_match_existing_asset_is_type_scoped(tmp_path):
    """Rebuild must never attach a PDF to a video resource."""
    (tmp_path / "a.pdf").write_text("x", encoding="utf-8")
    (tmp_path / "b.mp4").write_bytes(b"x")
    (tmp_path / "c.mp4").write_bytes(b"x")
    video = ingest_router._match_existing_asset("video", tmp_path)
    pdf = ingest_router._match_existing_asset("pdf", tmp_path)
    assert video == "b.mp4"  # sorted + type-filtered
    assert pdf == "a.pdf"
    assert ingest_router._match_existing_asset("note", tmp_path) is None


# --- Idempotent re-ingestion -------------------------------------------------


def test_reingest_same_file_reuses_resource_id(client, monkeypatch):
    c, tmp = client
    (tmp / "notes.txt").write_text("hello world", encoding="utf-8")
    r1 = c.post("/courses/demo/ingest", json={"path": "notes.txt"})
    assert r1.status_code == 202
    r2 = c.post("/courses/demo/ingest", json={"path": "notes.txt"})
    assert r2.status_code == 202
    b1, b2 = r1.json(), r2.json()
    assert b1["resource_id"] == b2["resource_id"]
    assert b1["job_id"] != b2["job_id"]
    # No duplicate resource rows for the same asset.
    rids = [
        rid
        for rid in ingest_router._resources_by_course["demo"]
        if ingest_router._resources[rid].get("asset_name") == "notes.txt"
    ]
    assert rids == [b1["resource_id"]]


def test_reingest_different_file_creates_new_resource(client):
    c, tmp = client
    (tmp / "a.txt").write_text("alpha", encoding="utf-8")
    (tmp / "b.txt").write_text("bravo", encoding="utf-8")
    r1 = c.post("/courses/demo/ingest", json={"path": "a.txt"})
    r2 = c.post("/courses/demo/ingest", json={"path": "b.txt"})
    assert r1.status_code == 202 and r2.status_code == 202
    assert r1.json()["resource_id"] != r2.json()["resource_id"]
    assert len(ingest_router._resources_by_course["demo"]) == 2


def test_reingest_does_not_accumulate_chunks(client, monkeypatch):
    c, tmp = client
    (tmp / "notes.txt").write_text("hello world", encoding="utf-8")
    r1 = c.post("/courses/demo/ingest", json={"path": "notes.txt"})
    n1 = len(get_shared_store()._chunks)
    r2 = c.post("/courses/demo/ingest", json={"path": "notes.txt"})
    n2 = len(get_shared_store()._chunks)
    assert r1.json()["resource_id"] == r2.json()["resource_id"]
    assert n2 == n1  # re-ingestion replaces, never appends


def test_reingest_after_failed_job_reuses_resource_id(client):
    """A failed job must not strand its resource and spawn a duplicate.

    Regression: a failed ingest leaves the row at ``failed`` with its old
    chunks still indexed. Matching on ``done``/``pending`` only made the next
    re-ingest mint a fresh id, so the stale resource stayed in course scope
    forever and competed with the corrected text during retrieval.
    """
    c, tmp = client
    (tmp / "notes.txt").write_text("hello world", encoding="utf-8")
    r1 = c.post("/courses/demo/ingest", json={"path": "notes.txt"})
    stale_rid = r1.json()["resource_id"]
    # Simulate the worker failing: row goes to ``failed``, chunks stay indexed.
    ingest_router._resources[stale_rid]["status"] = "failed"

    r2 = c.post("/courses/demo/ingest", json={"path": "notes.txt"})

    assert r2.json()["resource_id"] == stale_rid
    assert r2.json()["job_id"] != r1.json()["job_id"]
    rids = [
        rid
        for rid in ingest_router._resources_by_course["demo"]
        if ingest_router._resources[rid].get("asset_name") == "notes.txt"
    ]
    assert rids == [stale_rid]
    # Reused row is revived (job runs inline, so it lands on done/failed) and
    # the previous failure's error field is cleared.
    assert ingest_router._resources[stale_rid]["status"] != "failed"
    assert not ingest_router._resources[stale_rid].get("error")


def test_reingest_does_not_reuse_other_type_resource(client):
    """asset_name match is type-scoped, so a note never steals a video row."""
    c, tmp = client
    (tmp / "clip.mp4").write_bytes(b"x")
    (tmp / "clip.txt").write_text("hi", encoding="utf-8")
    video = c.post("/courses/demo/ingest", json={"path": "clip.mp4"})
    note = c.post("/courses/demo/ingest", json={"path": "clip.txt"})
    assert video.json()["resource_id"] != note.json()["resource_id"]


# --- Live-only (skipped offline) ---------------------------------------------


@pytest.mark.skip(reason="needs GEMINI key + network")
def test_live_gemini_embed():  # pragma: no cover
    from app.ingestion.embedder import GeminiEmbedder

    vecs = GeminiEmbedder().embed_batch(["hello world"])
    assert len(vecs[0]) == 3072


@pytest.mark.skip(reason="needs network")
def test_live_youtube_metadata():  # pragma: no cover
    from app.ingestion.youtube import fetch_metadata

    meta = fetch_metadata("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert meta.title
