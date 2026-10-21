"""Practice quizzes: same gates as chat, every question cited, cheap grading.

The generation tests run against a real Chroma store so the strict-T gate is
the store's own ``where`` clause.
"""

from __future__ import annotations

import asyncio
import json
import random

import pytest
from fastapi.testclient import TestClient

from app.chat import router as chat_router
from app.chat.launch import create_session_token
from app.chat.llm import LLMResult
from app.chat.quiz import (
    GradeRequest,
    QuizRequest,
    QuizService,
    parse_json_object,
    validate_questions,
)
from app.chat.service import ChatService
from app.chat.sessions import InMemorySessionStore, session_store
from app.config import settings
from app.main import app
from tests.store_helpers import make_chunk

T = 120_000
TEXT = "partial derivatives hold one variable constant"


class _ScriptedLLM:
    """Replies with a fixed text and records the prompt it was shown."""

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    async def complete(self, *, system, user_prompt, passages, mode) -> LLMResult:
        self.prompts.append(user_prompt)
        return LLMResult(text=self.reply)


def _mcq(cid, **kw):
    return {
        "type": "mcq", "prompt": "Which is held constant?", "chunk_id": cid,
        "options": ["the other variable", "nothing", "both", "time"],
        "answer_index": 0, "explanation": "By definition.", **kw,
    }


def _setup(chroma_store, embedder, reply: str):
    chroma_store.add([
        make_chunk("open_early", resource_id="res_open", end_ms=60_000, text=TEXT),
        make_chunk("open_late", resource_id="res_open", start_ms=800_000, end_ms=900_000, text=TEXT),
        make_chunk("done_late", resource_id="res_done", start_ms=2_900_000, end_ms=3_000_000, text=TEXT),
        make_chunk("other_pdf", resource_id="res_other", source_type="pdf",
                   start_ms=None, end_ms=None, page_no=2, video_id=None, text=TEXT),
    ])
    sessions = InMemorySessionStore()
    llm = _ScriptedLLM(reply)
    svc = QuizService(store=chroma_store, embedder=embedder, llm=llm, sessions=sessions)
    session = sessions.create(
        learner_id="l1", course_id="course_1", resource_id="res_open", lesson_id=None,
        resource_scope=["res_open", "res_done", "res_other"], current_time_ms=T,
    )
    return svc, session.id, llm


def _quiz(svc, sid, **kw):
    req = QuizRequest(session_id=sid, current_time_ms=T, video_id="res_open", **kw)
    return asyncio.run(svc.make_quiz(req))


def test_quiz_reads_open_material_at_t_plus_done_and_drops_uncited(chroma_store, embedder):
    reply = json.dumps({"questions": [
        _mcq("open_early"),
        _mcq("done_late"),
        _mcq("open_late"),  # past T: never retrieved, so never valid
        _mcq("other_pdf"),  # granted but not done
        _mcq("made_up_id"),
    ]})
    svc, sid, llm = _setup(chroma_store, embedder, reply)
    resp = _quiz(svc, sid, completed_resource_ids=["res_done"])
    assert not resp.refused
    assert [q.citation.chunk_id for q in resp.questions] == ["open_early", "done_late"]
    shown = llm.prompts[0]
    assert "open_early" in shown and "done_late" in shown
    assert "open_late" not in shown and "other_pdf" not in shown
    assert shown.count('open="true"') == 1  # only the open material is marked


def test_quiz_refuses_when_nothing_is_covered(chroma_store, embedder):
    svc, sid, llm = _setup(chroma_store, embedder, "{}")
    req = QuizRequest(session_id=sid, current_time_ms=10_000, video_id="res_open")
    resp = asyncio.run(svc.make_quiz(req))
    assert resp.refused and resp.questions == []
    assert llm.prompts == []  # no LLM call without material


def test_quiz_refuses_on_unusable_model_reply(chroma_store, embedder):
    svc, sid, _ = _setup(chroma_store, embedder, "Sure! Here is your quiz: 1) ...")
    resp = _quiz(svc, sid)
    assert resp.refused and resp.questions == []


def test_validation_per_type():
    class P:
        chunk_id, source_type, video_id = "c1", "pdf", None
        start_ms = end_ms = slide_no = image_no = None
        page_no, text, kind, metadata = 1, "t", None, None

    raw = {"questions": [
        _mcq("c1"),
        _mcq("c1", answer_index=7),  # out of range
        _mcq("c1", options=["a", "a", "b", "c"]),  # duplicate options
        {"type": "numerical", "prompt": "f_x at (1,2)?", "answer": "4", "tolerance": 0,
         "unit": "", "chunk_id": "c1"},
        {"type": "numerical", "prompt": "?", "answer": "four", "chunk_id": "c1"},
        {"type": "subjective", "prompt": "Why?", "model_answer": "Because. [chunk_id=c1]",
         "key_points": ["held constant", ""], "chunk_id": "c1"},
        {"type": "subjective", "prompt": "Why?", "chunk_id": "c1"},  # no model answer
        {"type": "essay", "prompt": "?", "chunk_id": "c1"},
    ]}
    qs = validate_questions(raw, [P()], random.Random(1))
    assert [q.type for q in qs] == ["mcq", "numerical", "subjective"]
    mcq, num, subj = qs
    assert mcq.options[mcq.answer_index] == "the other variable"  # survives the shuffle
    assert num.answer == 4 and num.tolerance > 0 and num.unit is None
    assert subj.model_answer == "Because." and subj.key_points == ["held constant"]


def test_parse_json_object_tolerates_fences_and_single_latex_backslashes():
    raw = '```json\n{"questions": [{"prompt": "Find $\\frac{\\partial f}{\\partial x}$"}]}\n```'
    obj = parse_json_object(raw)
    assert obj["questions"][0]["prompt"] == "Find $\\frac{\\partial f}{\\partial x}$"
    assert parse_json_object("no json here") is None


def _grade_req(answer: str) -> GradeRequest:
    return GradeRequest(
        session_id="s", prompt="Why hold y constant?",
        model_answer="To see how f changes with x alone.",
        key_points=["change with x alone", "y held constant"], answer=answer,
    )


def test_grade_uses_model_score_and_derives_verdict():
    llm = _ScriptedLLM('{"score": 0.55, "verdict": "correct", "feedback": "Half there.", "missing": ["y"]}')
    svc = QuizService(store=None, embedder=None, llm=llm, sessions=None)
    g = asyncio.run(svc.grade(_grade_req("x only")))
    assert g.score == 0.55 and g.verdict == "partial" and g.missing == ["y"]
    assert "<student_answer>x only</student_answer>" in llm.prompts[0]


def test_grade_falls_back_to_key_points_without_a_model():
    svc = QuizService(store=None, embedder=None, llm=_ScriptedLLM("unavailable"), sessions=None)
    full = asyncio.run(svc.grade(_grade_req("we look at the change with x alone, y held constant")))
    none = asyncio.run(svc.grade(_grade_req("no idea")))
    assert full.verdict == "correct" and none.verdict == "incorrect"


@pytest.fixture
def api(chroma_store, embedder):
    chat_router.reset_chat_state()
    chroma_store.add([make_chunk("open_early", resource_id="r1", end_ms=60_000, text=TEXT)])
    chat_router.set_service_override(ChatService(store=chroma_store, embedder=embedder))
    session = session_store.create(
        learner_id="l1", course_id="course_1", resource_id="r1", lesson_id=None,
        resource_scope=["r1"], current_time_ms=T,
    )
    token = create_session_token(settings.lms_launch_secret, session.id, "l1")
    yield TestClient(app), session.id, token
    chat_router.reset_chat_state()


def test_quiz_routes_need_the_session_token(api):
    client, sid, token = api
    body = {"session_id": sid, "current_time_ms": T, "video_id": "r1"}
    assert client.post("/quiz", json=body).status_code == 401
    r = client.post("/quiz", json=body, headers={"X-Session-Token": token})
    assert r.status_code == 200, r.text
    qs = r.json()["questions"]
    assert qs and qs[0]["citation"]["chunk_id"] == "open_early"
    grade = {"session_id": sid, "prompt": "Why?", "model_answer": "Because x.",
             "key_points": ["because"], "answer": "because x"}
    assert client.post("/quiz/grade", json=grade).status_code == 401
    g = client.post("/quiz/grade", json={**grade, "session_token": token})
    assert g.status_code == 200 and g.json()["verdict"] == "correct"
