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
from app.ingestion.models import Chunk, Segment, text_hash
from app.ingestion.transcribe import (
    parse_transcript_json,
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
    svc = ChatService()  # default store -> shared registry
    assert svc._store is store
    passages = asyncio.run(
        svc._retrieval().retrieve("cellular energy", "demo", ["r1"], 60_000, k=5)
    )
    assert [p.chunk_id for p in passages] == ["reg1"]
    reset_shared_store()


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
