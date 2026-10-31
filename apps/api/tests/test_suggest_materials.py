"""Suggesting materials a chat cannot read: ids only, granted ones only."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.chat.llm import LLMResult
from app.chat.schemas import ChatResponse
from app.chat.service import ChatService
from app.chat.sessions import InMemoryMessageStore, InMemorySessionStore
from app.chat.suggest import (
    MAX_SUGGESTIONS,
    pick_suggestions,
    suggest_candidates,
    suggest_materials,
)
from tests.store_helpers import make_chunk

TEXT = "partial derivatives hold one variable constant"


def _p(rid: str, score: float):
    return SimpleNamespace(resource_id=rid, score=score)


def test_candidates_are_granted_and_not_already_readable():
    granted = ["a", "b", "c"]
    assert suggest_candidates(granted, ["a"], ["a", "b", "zzz"]) == ["b"]
    assert suggest_candidates(granted, [], ["c", "b"]) == ["b", "c"]
    assert suggest_candidates(granted, granted, ["a", "b"]) == []


def test_pick_keeps_good_matches_distinct_best_first():
    got = pick_suggestions([_p("x", 0.7), _p("y", 0.9), _p("x", 0.95), _p("z", 0.2)], min_score=0.5)
    assert got == ["x", "y"]


def test_pick_is_capped():
    got = pick_suggestions([_p(f"r{i}", 0.9) for i in range(10)], min_score=0.5)
    assert len(got) == MAX_SUGGESTIONS


class _NoLLM:
    async def complete(self, **_kw) -> LLMResult:  # pragma: no cover - not reached
        raise AssertionError("suggestion must not call the model")


def test_suggest_materials_searches_only_candidates(chroma_store, embedder):
    chroma_store.add([
        make_chunk("late_video", resource_id="res_video", start_ms=2_900_000, end_ms=3_000_000, text=TEXT),
        make_chunk("secret_pdf", resource_id="res_secret", source_type="pdf",
                   start_ms=None, end_ms=None, page_no=1, video_id=None, text=TEXT),
    ])
    sessions = InMemorySessionStore()
    service = ChatService(
        store=chroma_store, embedder=embedder, llm=_NoLLM(), sessions=sessions,
        messages=InMemoryMessageStore(),
    )
    session = sessions.create(
        learner_id="l1", course_id="course_1", resource_id="res_video", lesson_id=None,
        resource_scope=["res_video"], current_time_ms=0,
    )
    cands = suggest_candidates(session.resource_scope, [], ["res_video", "res_secret"])
    assert cands == ["res_video"]  # the ungranted pdf is never searched
    got = asyncio.run(suggest_materials(service, session, "what is a partial derivative", cands))
    assert set(got) <= {"res_video"}


def test_suggest_materials_never_raises():
    broken = SimpleNamespace(_retrieval=lambda: (_ for _ in ()).throw(RuntimeError("down")))
    session = SimpleNamespace(course_id="c")
    assert asyncio.run(suggest_materials(broken, session, "q", ["a"])) == []
    assert asyncio.run(suggest_materials(broken, session, "q", [])) == []


def test_response_defaults_to_no_suggestions():
    assert ChatResponse(answer="x").suggested_resource_ids == []


class _RecordingLLM:
    """Answers anything; records whether it was handed passages."""

    def __init__(self) -> None:
        self.passages: list | None = None

    async def complete(self, *, system, user_prompt, passages, mode) -> LLMResult:
        self.passages = list(passages)
        return LLMResult(text="Hello! Happy to help.", cited_ids=[])


def _empty_allowance_service(chroma_store, embedder):
    chroma_store.add([make_chunk("pdf1", resource_id="res_pdf", source_type="pdf",
                                 start_ms=None, end_ms=None, page_no=1, video_id=None, text=TEXT)])
    sessions = InMemorySessionStore()
    llm = _RecordingLLM()
    service = ChatService(
        store=chroma_store, embedder=embedder, llm=llm, sessions=sessions,
        messages=InMemoryMessageStore(),
    )
    session = sessions.create(
        learner_id="l1", course_id="course_1", resource_id="res_pdf", lesson_id=None,
        resource_scope=["res_pdf"], current_time_ms=0,
    )
    return service, session.id, llm


def test_chat_with_nothing_allowed_still_greets(chroma_store, embedder):
    service, sid, _llm = _empty_allowance_service(chroma_store, embedder)
    resp = asyncio.run(service.chat(
        session_id=sid, current_time_ms=0, message="hi, how are you", scope_resource_ids=[],
    ))
    assert not resp.scope_empty and not resp.refused
    assert resp.mode == "conversational"
    assert resp.citations == []


def test_chat_with_nothing_allowed_answers_generally_without_reading(chroma_store, embedder):
    service, sid, llm = _empty_allowance_service(chroma_store, embedder)
    resp = asyncio.run(service.chat(
        session_id=sid, current_time_ms=0, message="what is a partial derivative",
        scope_resource_ids=[],
    ))
    assert not resp.scope_empty
    assert resp.mode == "general"
    assert resp.citations == []
    assert llm.passages == []  # no material was read


def test_stale_grant_still_fails_closed(chroma_store, embedder):
    service, sid, _llm = _empty_allowance_service(chroma_store, embedder)
    resp = asyncio.run(service.chat(
        session_id=sid, current_time_ms=0, message="what is a partial derivative",
        focus_resource_id="res_not_granted",
    ))
    assert resp.scope_empty and resp.refused
