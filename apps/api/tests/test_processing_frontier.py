"""Processing frontier: partial videos, range delete, honest not-processed answer."""

from __future__ import annotations

import asyncio

from app.chat.llm import LLMResult
from app.chat.prompts import REFUSAL_TEXT
from app.chat.service import PROCESSING_PENDING_TEXT, ChatService
from app.chat.sessions import InMemoryMessageStore, InMemorySessionStore
from tests.store_helpers import make_chunk

RES = "res_a"


def _chunk(cid, rid=RES, start=0, end=60_000, frontier=None, duration=None):
    c = make_chunk(cid, resource_id=rid, start_ms=start, end_ms=end)
    if frontier is not None:
        c.metadata["processed_through_ms"] = frontier
    if duration is not None:
        c.metadata["duration_ms"] = duration
    return c


class _LLM:
    def __init__(self):
        self.calls = 0

    async def complete(self, *, system, user_prompt, passages, mode) -> LLMResult:
        self.calls += 1
        if passages:
            return LLMResult(
                text=f"Answer [chunk_id={passages[0].chunk_id}]",
                cited_ids=[passages[0].chunk_id],
            )
        return LLMResult(text=REFUSAL_TEXT, cited_ids=[])


def _chat(store, embedder, t_ms, focus=RES):
    sessions = InMemorySessionStore()
    llm = _LLM()
    svc = ChatService(
        store=store,
        embedder=embedder,
        llm=llm,
        sessions=sessions,
        messages=InMemoryMessageStore(),
    )
    s = sessions.create(
        learner_id="l1",
        course_id="course_1",
        resource_id=RES,
        lesson_id=None,
        resource_scope=[RES],
        current_time_ms=t_ms,
    )
    resp = asyncio.run(
        svc.chat(
            session_id=s.id,
            current_time_ms=t_ms,
            message="what are partial derivatives?",
            focus_resource_id=focus,
        )
    )
    return resp, llm


def _by_id(store):
    return {r["id"]: r for r in store.resources("course_1")}


def test_marker_persisted_and_maxed(chroma_store):
    chroma_store.add(
        [
            _chunk("c1", end=60_000, frontier=60_000, duration=300_000),
            _chunk("c2", start=60_000, end=120_000, frontier=120_000, duration=300_000),
        ]
    )
    got = chroma_store.processing_frontier(RES)
    assert got["processed_through_ms"] == 120_000
    assert got["duration_ms"] == 300_000
    assert _by_id(chroma_store)[RES]["processed_through_ms"] == 120_000


def test_status_partial_ready_legacy(chroma_store):
    chroma_store.add(
        [
            _chunk("p1", rid="res_partial", frontier=60_000, duration=300_000),
            _chunk("d1", rid="res_done", frontier=300_000, duration=300_000),
            _chunk("u1", rid="res_unknown_dur", frontier=60_000),
            _chunk("l1", rid="res_legacy"),
        ]
    )
    r = _by_id(chroma_store)
    assert r["res_partial"]["status"] == "partial"
    assert r["res_done"]["status"] == "ready"
    assert r["res_unknown_dur"]["status"] == "ready"
    assert r["res_unknown_dur"]["processed_through_ms"] == 60_000
    assert r["res_legacy"]["status"] == "ready"
    assert "processed_through_ms" not in r["res_legacy"]
    assert "_duration_ms" not in r["res_partial"]


def test_delete_resource_range_exact(chroma_store):
    chroma_store.add(
        [
            _chunk("a1", rid="res_a", start=0, end=60_000),
            _chunk("a2", rid="res_a", start=60_000, end=120_000),
            _chunk("a3", rid="res_a", start=120_000, end=180_000),
            _chunk("b1", rid="res_a2", start=60_000, end=120_000),
        ]
    )
    n = chroma_store.delete_resource_range("res_a", 60_000, 120_000)
    assert n == 1
    ids = set(chroma_store._collection.get()["ids"])
    assert ids == {"a1", "a3", "b1"}
    assert chroma_store.delete_resource_range("res_a", 60_000, 120_000) == 0


def test_chat_past_frontier_is_honest(chroma_store, embedder):
    chroma_store.add([_chunk("c1", end=60_000, frontier=60_000, duration=300_000)])
    resp, llm = _chat(chroma_store, embedder, 200_000)
    assert resp.processing_pending is True
    assert resp.processed_through_ms == 60_000
    assert resp.refused and not resp.grounded
    assert resp.mode == "grounded" and resp.citations == []
    assert resp.answer == PROCESSING_PENDING_TEXT
    assert llm.calls == 0


def test_chat_at_or_below_frontier_grounded(chroma_store, embedder):
    chroma_store.add(
        [
            _chunk("c1", end=60_000, frontier=120_000, duration=300_000),
            _chunk("c2", start=60_000, end=120_000, frontier=120_000, duration=300_000),
        ]
    )
    resp, llm = _chat(chroma_store, embedder, 120_000)
    assert resp.processing_pending is False
    assert resp.grounded and not resp.refused
    assert llm.calls >= 1
    assert all(c.end_ms <= 120_000 for c in resp.citations)


def test_chat_no_marker_unchanged(chroma_store, embedder):
    chroma_store.add([_chunk("c1", end=60_000)])
    resp, _ = _chat(chroma_store, embedder, 200_000)
    assert resp.processing_pending is False
    assert resp.processed_through_ms is None
    assert resp.grounded and not resp.refused
