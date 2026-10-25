"""Automatic chapters: generation, validation, storage and the GET route.

Real Chroma store in tmp_path (no doubles: the reader uses the store's own
exact-id ``get`` and ``_to_passage``), chapter files in tmp_path, the LLM
replaced by a function returning canned text, and background generation
captured so each test decides when it runs.
"""

from __future__ import annotations

import json
from itertools import pairwise

import pytest
from fastapi.testclient import TestClient

from app.ingestion import chapters as ch
from tests.store_helpers import make_chunk, make_embedder


@pytest.fixture(scope="module")
def _module_store(tmp_path_factory):
    """One Chroma store for the whole module.

    A store per test holds its sqlite/HNSW handles open for the rest of the
    run; sixteen more of them pushed the full suite past the per-process
    file-handle limit. Each test wipes the ids it uses instead.
    """
    from app.retrieval.store import ChromaVectorStore

    return ChromaVectorStore(
        path=tmp_path_factory.mktemp("chapters") / "chroma",
        embedder=make_embedder(), collection="chapter_chunks",
    )


@pytest.fixture
def chroma_store(_module_store):
    for rid in ("res_vid", "res_pdf", "res_other"):
        _module_store.delete_resource(rid)
    return _module_store


@pytest.fixture(autouse=True)
def _isolated_chapters(tmp_path, monkeypatch):
    monkeypatch.setattr(ch, "CHAPTERS_DIR", tmp_path / "chapters")
    monkeypatch.setattr(ch, "LLM_COMPLETE", lambda system, prompt: None)
    ch.reset_inflight()
    yield
    ch.reset_inflight()


@pytest.fixture
def spawned(monkeypatch):
    """Capture background generations instead of starting threads."""
    queued: list = []
    monkeypatch.setattr(ch, "SPAWN", queued.append)
    return queued


def _run_all(queued: list) -> None:
    while queued:
        queued.pop(0)()


def _video(store, rid="res_vid", *, minutes=20, step_s=30, upto_ms=None, frontier=None,
           duration=None):
    """Index a video as ``step_s``-second transcript chunks."""
    end_total = upto_ms if upto_ms is not None else minutes * 60_000
    chunks = []
    for i, s in enumerate(range(0, end_total, step_s * 1000)):
        e = min(s + step_s * 1000, end_total)
        c = make_chunk(f"{rid}_c{i}", resource_id=rid, start_ms=s, end_ms=e,
                       text=f"topic {s // 300_000} lecture words about section {i}")
        if frontier is not None:
            c.metadata["processed_through_ms"] = frontier
        if duration is not None:
            c.metadata["duration_ms"] = duration
        chunks.append(c)
    store.add(chunks)


def _pdf(store, rid="res_pdf", pages=4, headings=None):
    chunks = []
    for n in range(1, pages + 1):
        head = (headings or {}).get(n, f"Heading {n}")
        chunks.append(make_chunk(
            f"{rid}_p{n}", resource_id=rid, source_type="pdf", start_ms=None, end_ms=None,
            page_no=n, video_id=None, text=f"{head}\nbody text of page {n}",
        ))
    store.add(chunks)


def _assert_contiguous_video(chapters, end_ms):
    assert chapters[0]["start_ms"] == 0
    assert chapters[-1]["end_ms"] == end_ms
    for a, b in pairwise(chapters):
        assert a["end_ms"] == b["start_ms"]
    for c in chapters:
        assert c["start_ms"] < c["end_ms"]
        assert c["page_start"] is None and c["slide_start"] is None


# ---------------------------------------------------------------------------
# Fallback
# ---------------------------------------------------------------------------


def test_video_fallback_is_contiguous_over_indexed_range(chroma_store):
    _video(chroma_store, minutes=20)
    record = ch.generate_and_store("res_vid", store=chroma_store)
    assert record["method"] == "fallback"
    chapters = record["chapters"]
    assert len(chapters) == 4  # ~5-minute spans
    _assert_contiguous_video(chapters, 20 * 60_000)
    assert chapters[0]["title"].startswith("Part 1: topic 0")
    assert (ch.chapters_dir() / "res_vid.json").exists()


def test_doc_fallback_groups_by_page_heading(chroma_store):
    _pdf(chroma_store, pages=5, headings={1: "Intro", 2: "Limits", 3: "Limits", 4: "Series",
                                          5: "Series"})
    chapters = ch.generate_and_store("res_pdf", store=chroma_store)["chapters"]
    assert [c["title"] for c in chapters] == ["Intro", "Limits", "Series"]
    assert [(c["page_start"], c["page_end"]) for c in chapters] == [(1, 1), (2, 3), (4, 5)]
    assert all(c["start_ms"] is None and c["slide_start"] is None for c in chapters)


def test_doc_fallback_fixed_groups_when_too_many_headings(chroma_store):
    _pdf(chroma_store, pages=25)
    chapters = ch.generate_and_store("res_pdf", store=chroma_store)["chapters"]
    assert 1 <= len(chapters) <= ch.MAX_DOC_CHAPTERS
    assert chapters[0]["page_start"] == 1 and chapters[-1]["page_end"] == 25
    for a, b in pairwise(chapters):
        assert b["page_start"] == a["page_end"] + 1


# ---------------------------------------------------------------------------
# LLM validation
# ---------------------------------------------------------------------------


def test_llm_chapters_are_clamped_snapped_and_repaired(chroma_store, monkeypatch):
    _video(chroma_store, minutes=20)
    reply = json.dumps({"chapters": [
        {"title": "Chain Rule", "start_s": 610},          # snapped to 600 s
        {"title": "Partial Derivatives", "start_s": 15},  # first -> forced to 0
        {"title": "Chain Rule Again", "start_s": 600},    # duplicate boundary dropped
        {"title": "dy comes out to 2xy", "start_s": 900},  # result, not a topic
        {"title": "Wrap Up", "start_s": 99_999},          # clamped to the end
        {"title": "", "start_s": 300},                    # empty title dropped
    ]})
    monkeypatch.setattr(ch, "LLM_COMPLETE", lambda s, p: "```json\n" + reply + "\n```")
    record = ch.generate_and_store("res_vid", store=chroma_store)
    assert record["method"] == "llm"
    chapters = record["chapters"]
    assert [c["title"] for c in chapters] == ["Partial Derivatives", "Chain Rule", "Wrap Up"]
    assert chapters[1]["start_ms"] == 600_000
    _assert_contiguous_video(chapters, 20 * 60_000)


def test_llm_doc_chapters_by_page(chroma_store, monkeypatch):
    _pdf(chroma_store, pages=6)
    reply = '{"chapters": [{"title": "Basics", "start": 1}, {"title": "Applications", "start": 4}]}'
    monkeypatch.setattr(ch, "LLM_COMPLETE", lambda s, p: reply)
    chapters = ch.generate_and_store("res_pdf", store=chroma_store)["chapters"]
    assert [(c["title"], c["page_start"], c["page_end"]) for c in chapters] == [
        ("Basics", 1, 3), ("Applications", 4, 6)]


@pytest.mark.parametrize("junk", [
    "I could not do that.",
    '{"chapters": "none"}',
    '{"chapters": [{"title": "x = 2y", "start_s": 0}]}',
    '[{"start_s": 0}]',
])
def test_junk_llm_output_falls_back(chroma_store, monkeypatch, junk):
    _video(chroma_store, minutes=10)
    monkeypatch.setattr(ch, "LLM_COMPLETE", lambda s, p: junk)
    record = ch.generate_and_store("res_vid", store=chroma_store)
    assert record["method"] == "fallback"
    _assert_contiguous_video(record["chapters"], 10 * 60_000)


def test_llm_exception_falls_back(chroma_store, monkeypatch):
    _video(chroma_store, minutes=10)

    def boom(s, p):
        raise RuntimeError("quota")

    monkeypatch.setattr(ch, "LLM_COMPLETE", boom)
    assert ch.generate_and_store("res_vid", store=chroma_store)["method"] == "fallback"


def test_digest_is_capped_and_thinned_evenly(chroma_store):
    _video(chroma_store, minutes=120, step_s=5)
    view = ch.read_resource("res_vid", store=chroma_store)
    prompt = ch.build_prompt(view)
    assert len(prompt) < ch.DIGEST_CHAR_CAP + 2_000
    # The end of the lecture is still represented after thinning.
    assert "[71" in prompt.split("\n")[-1][:3] or int(
        prompt.rsplit("[", 1)[1].split("s]")[0]) > 6_000


# ---------------------------------------------------------------------------
# GET route
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def _module_client():
    from app.main import app

    # Not a context manager: the lifespan would turn on session/subject
    # persistence against the developer's data/ directory.
    return TestClient(app)


@pytest.fixture
def client(_module_client, chroma_store, monkeypatch):
    from app.retrieval import registry

    monkeypatch.setattr(registry, "get_shared_store", lambda: chroma_store)
    return _module_client


def test_get_pending_then_ready(client, chroma_store, spawned):
    _video(chroma_store, minutes=10)
    first = client.get("/resources/res_vid/chapters")
    assert first.status_code == 200
    assert first.json() == {"resource_id": "res_vid", "status": "pending", "chapters": []}
    assert len(spawned) == 1
    # A second poll while running does not queue a duplicate.
    assert client.get("/resources/res_vid/chapters").json()["status"] == "pending"
    assert len(spawned) == 1
    _run_all(spawned)
    body = client.get("/resources/res_vid/chapters").json()
    assert body["status"] == "ready"
    _assert_contiguous_video(body["chapters"], 10 * 60_000)
    assert set(body["chapters"][0]) == {"title", "start_ms", "end_ms", "page_start",
                                        "page_end", "slide_start", "slide_end"}
    assert not spawned


def test_get_unknown_resource_is_unavailable(client, spawned):
    resp = client.get("/resources/res_nope/chapters")
    assert resp.status_code == 200
    assert resp.json() == {"resource_id": "res_nope", "status": "unavailable", "chapters": []}
    assert not spawned


def test_partial_video_regenerates_when_frontier_advances(client, chroma_store, spawned):
    duration = 20 * 60_000
    _video(chroma_store, upto_ms=4 * 60_000, frontier=4 * 60_000, duration=duration)
    client.get("/resources/res_vid/chapters")
    _run_all(spawned)
    body = client.get("/resources/res_vid/chapters").json()
    assert body["status"] == "ready"
    assert body["chapters"][-1]["end_ms"] == 4 * 60_000
    assert ch.load_record("res_vid")["through_ms"] == 4 * 60_000

    # A small advance (< 2 min) keeps the stored chapters.
    _video(chroma_store, upto_ms=5 * 60_000, frontier=5 * 60_000, duration=duration)
    assert client.get("/resources/res_vid/chapters").json()["status"] == "ready"
    assert not spawned

    # A large advance rebuilds; the old list is shown while pending.
    _video(chroma_store, upto_ms=12 * 60_000, frontier=12 * 60_000, duration=duration)
    pending = client.get("/resources/res_vid/chapters").json()
    assert pending["status"] == "pending" and pending["chapters"]
    _run_all(spawned)
    body = client.get("/resources/res_vid/chapters").json()
    assert body["status"] == "ready"
    _assert_contiguous_video(body["chapters"], 12 * 60_000)
    assert ch.load_record("res_vid")["through_ms"] == 12 * 60_000

    # Fully processed: rebuilt even though the last step is small.
    _video(chroma_store, upto_ms=duration, frontier=duration, duration=duration)
    assert client.get("/resources/res_vid/chapters").json()["status"] == "pending"
    _run_all(spawned)
    record = ch.load_record("res_vid")
    assert record["complete"] and record["through_ms"] == duration


def test_ingest_hook_schedules_and_never_raises(chroma_store, monkeypatch, spawned):
    from app.retrieval import registry

    monkeypatch.setattr(registry, "get_shared_store", lambda: chroma_store)
    _video(chroma_store, minutes=6)
    monkeypatch.setattr(ch, "AUTO_GENERATE", False)
    ch.on_ingest_done("res_vid")
    assert not spawned
    monkeypatch.setattr(ch, "AUTO_GENERATE", True)
    ch.on_ingest_done("res_vid")
    _run_all(spawned)
    assert ch.load_record("res_vid")["method"] == "fallback"

    def broken(fn):
        raise RuntimeError("no threads")

    monkeypatch.setattr(ch, "SPAWN", broken)
    ch.on_ingest_done("res_other")  # must not raise
    assert not ch.is_inflight("res_other")


def test_delete_chapters_and_bad_ids(chroma_store):
    _pdf(chroma_store, pages=2)
    ch.generate_and_store("res_pdf", store=chroma_store)
    ch.delete_chapters("res_pdf")
    assert ch.load_record("res_pdf") is None
    ch.delete_chapters("../etc")  # never resolves outside the directory
    assert ch.load_record("../etc") is None
