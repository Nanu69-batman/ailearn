"""A chat reads the open material plus the materials marked done -- nothing else.

Runs against a real Chroma store so the strict-T gate under test is the
store's own ``where`` clause, not a fake's.
"""

from __future__ import annotations

import asyncio

from app.chat.completed import completed_scope
from app.chat.llm import LLMResult
from app.chat.service import ChatService
from app.chat.sessions import InMemoryMessageStore, InMemorySessionStore
from tests.store_helpers import make_chunk

T = 120_000  # the open video is paused at 2:00
TEXT = "partial derivatives hold one variable constant"


class _CitingLLM:
    """Cites every passage it is given and records which ones those were."""

    def __init__(self) -> None:
        self.seen: list[str] = []

    async def complete(self, *, system, user_prompt, passages, mode) -> LLMResult:
        self.seen = [p.chunk_id for p in passages]
        body = " ".join(f"Point [chunk_id={cid}]." for cid in self.seen)
        return LLMResult(text=body, cited_ids=list(self.seen))


def _setup(chroma_store, embedder):
    chroma_store.add([
        # The open video: one passage before T, one after.
        make_chunk("open_early", resource_id="res_open", end_ms=60_000, text=TEXT),
        make_chunk("open_late", resource_id="res_open", start_ms=800_000, end_ms=900_000, text=TEXT),
        # A video marked done: readable in full, but an unknown end stays out.
        make_chunk("done_late", resource_id="res_done", start_ms=2_900_000, end_ms=3_000_000, text=TEXT),
        make_chunk("done_noend", resource_id="res_done", start_ms=10_000, end_ms=None, text=TEXT),
        # Granted, but not marked done.
        make_chunk("other_pdf", resource_id="res_other", source_type="pdf",
                   start_ms=None, end_ms=None, page_no=2, video_id=None, text=TEXT),
        # Not granted to this session at all.
        make_chunk("secret_pdf", resource_id="res_secret", source_type="pdf",
                   start_ms=None, end_ms=None, page_no=1, video_id=None, text=TEXT),
    ])
    sessions, messages = InMemorySessionStore(), InMemoryMessageStore()
    llm = _CitingLLM()
    service = ChatService(
        store=chroma_store, embedder=embedder, llm=llm, sessions=sessions, messages=messages,
    )
    session = sessions.create(
        learner_id="l1", course_id="course_1", resource_id="res_open", lesson_id=None,
        resource_scope=["res_open", "res_done", "res_other"], current_time_ms=T,
    )
    return service, session.id, llm


def _ask(service, sid, **kw):
    return asyncio.run(service.chat(
        session_id=sid, current_time_ms=T, message="what is a partial derivative",
        focus_resource_id="res_open", **kw,
    ))


def test_without_completed_materials_only_the_open_one_is_read(chroma_store, embedder):
    service, sid, llm = _setup(chroma_store, embedder)
    _ask(service, sid)
    assert set(llm.seen) == {"open_early"}


def test_completed_materials_are_read_in_full_and_nothing_else(chroma_store, embedder):
    service, sid, llm = _setup(chroma_store, embedder)
    resp = _ask(service, sid, completed_resource_ids=["res_done", "res_secret"])
    assert "done_late" in llm.seen  # past the open video's T: done means watched
    assert "open_early" in llm.seen
    assert "open_late" not in llm.seen  # the open video keeps its playhead gate
    assert "done_noend" not in llm.seen  # unknown end: still excluded
    assert "other_pdf" not in llm.seen  # granted but not done
    assert "secret_pdf" not in llm.seen  # never granted: done cannot widen
    assert {c.chunk_id for c in resp.citations} <= set(llm.seen)


def test_completed_scope_only_narrows_the_grant():
    grant = ["a", "b", "c"]
    assert completed_scope(grant, "a", ["b", "zzz"]) == ["b"]
    assert completed_scope(grant, "a", ["a"]) == []  # the open one keeps its gate
    assert completed_scope(grant, None, ["b"]) == []  # no focus: grant already in play
    assert completed_scope(grant, "a", []) == []
