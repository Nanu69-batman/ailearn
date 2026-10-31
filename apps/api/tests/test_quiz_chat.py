"""Quizzes asked from a Malai chat, and quiz events recorded in a chat.

A subject chat's quiz reads only that subject (documents, plus videos marked
done); a chat in no subject gets a general-knowledge quiz with no citations;
and a started/completed quiz is written into the chat by the server.
"""

from __future__ import annotations

from tests.conftest import SIGNED_OUT
import asyncio
import json

import pytest
from pydantic import ValidationError

from app.chat.quiz import (
    QUIZ_NO_TOPIC_TEXT,
    QuizNoteRequest,
    QuizRequest,
    QuizService,
    quiz_note_lines,
)
from app.chat.sessions import InMemoryMessageStore, InMemorySessionStore
from tests.store_helpers import make_chunk
from tests.test_quiz import TEXT, _mcq, _ScriptedLLM, api  # noqa: F401  (fixture)


def _malai(chroma_store, embedder, reply: str):
    chroma_store.add([
        make_chunk("vid_done", resource_id="res_vid_done", start_ms=600_000, end_ms=610_000, text=TEXT),
        make_chunk("vid_open", resource_id="res_vid_open", end_ms=10_000, text=TEXT),
        make_chunk("pdf_in", resource_id="res_pdf", source_type="pdf", start_ms=None,
                   end_ms=None, page_no=1, video_id=None, text=TEXT),
        make_chunk("pdf_other", resource_id="res_other_pdf", source_type="pdf", start_ms=None,
                   end_ms=None, page_no=1, video_id=None, text=TEXT),
    ])
    sessions = InMemorySessionStore()
    messages = InMemoryMessageStore()
    llm = _ScriptedLLM(reply)
    svc = QuizService(store=chroma_store, embedder=embedder, llm=llm, sessions=sessions,
                      messages=messages)
    sid = sessions.create(
        learner_id="l1", course_id="course_1", resource_id="res_pdf", lesson_id=None,
        resource_scope=["res_vid_done", "res_vid_open", "res_pdf", "res_other_pdf"],
        current_time_ms=0,
    ).id
    return svc, sid, llm, messages


def test_subject_chat_quiz_reads_only_the_subject_and_done_videos(chroma_store, embedder):
    reply = json.dumps({"questions": [
        _mcq("pdf_in"), _mcq("vid_done"), _mcq("vid_open"), _mcq("pdf_other"),
    ]})
    svc, sid, llm, _ = _malai(chroma_store, embedder, reply)
    req = QuizRequest(
        session_id=sid, current_time_ms=0, count=4,
        scope_resource_ids=["res_pdf", "res_vid_done", "res_vid_open"],
        completed_resource_ids=["res_vid_done"],
    )
    resp = asyncio.run(svc.make_quiz(req))
    # The undone video stays behind the T=0 gate; the other subject's PDF is
    # outside the chat's scope. Neither may be quizzed on.
    assert sorted(q.citation.chunk_id for q in resp.questions) == ["pdf_in", "vid_done"]
    shown = llm.prompts[0]
    assert "vid_open" not in shown and "pdf_other" not in shown


def test_quiz_prompt_carries_the_learners_instructions(chroma_store, embedder):
    svc, sid, llm, _ = _malai(chroma_store, embedder, json.dumps({"questions": [_mcq("pdf_in")]}))
    req = QuizRequest(session_id=sid, current_time_ms=0, count=1, scope_resource_ids=["res_pdf"],
                      prompt="only the hard parts")
    asyncio.run(svc.make_quiz(req))
    assert "<instructions>only the hard parts</instructions>" in llm.prompts[0]


def test_general_quiz_reads_no_materials_and_cites_nothing(chroma_store, embedder):
    q = _mcq("pdf_in")  # a chunk id the model invents is ignored, not trusted
    svc, sid, llm, _ = _malai(chroma_store, embedder, json.dumps({"questions": [q]}))
    req = QuizRequest(session_id=sid, current_time_ms=0, count=1, general=True,
                      prompt="thermodynamics")
    resp = asyncio.run(svc.make_quiz(req))
    assert len(resp.questions) == 1 and resp.questions[0].citation is None
    assert "<context>" not in llm.prompts[0] and TEXT not in llm.prompts[0]


def test_general_quiz_takes_its_topic_from_the_chat(chroma_store, embedder):
    svc, sid, llm, messages = _malai(chroma_store, embedder, json.dumps({"questions": [_mcq("x")]}))
    messages.append(sid, "user", "what is entropy?")
    messages.append(sid, "assistant", "Entropy measures disorder.")
    asyncio.run(svc.make_quiz(QuizRequest(session_id=sid, current_time_ms=0, general=True, count=1)))
    assert "Learner: what is entropy?" in llm.prompts[0]


def test_general_quiz_without_a_topic_asks_for_one(chroma_store, embedder):
    svc, sid, llm, _ = _malai(chroma_store, embedder, "{}")
    resp = asyncio.run(svc.make_quiz(QuizRequest(session_id=sid, current_time_ms=0, general=True)))
    assert resp.refused and resp.message == QUIZ_NO_TOPIC_TEXT and llm.prompts == []


def test_quiz_size_is_one_to_twenty():
    QuizRequest(session_id="s", current_time_ms=0, count=20)
    QuizRequest(session_id="s", current_time_ms=0, count=1)
    with pytest.raises(ValidationError):
        QuizRequest(session_id="s", current_time_ms=0, count=21)


def test_note_lines():
    started = quiz_note_lines(QuizNoteRequest(
        session_id="s", event="started", request="quiz me on entropy", count=5,
    ))
    assert started[0] == ("user", "quiz me on entropy")
    assert started[1][0] == "assistant" and "5-question quiz" in started[1][1]
    done = quiz_note_lines(QuizNoteRequest(
        session_id="s", event="completed", count=5, score=3.5,
        to_review=["What is entropy? [chunk_id=abc]"],
    ))
    assert len(done) == 1 and "3.5 / 5" in done[0][1]
    assert "- What is entropy?" in done[0][1] and "chunk_id" not in done[0][1]
    perfect = quiz_note_lines(QuizNoteRequest(session_id="s", event="completed", count=2, score=2))
    assert "Every answer was right" in perfect[0][1]


def test_note_route_writes_into_the_chat(api):  # noqa: F811
    client, sid, token = api
    body = {"session_id": sid, "event": "completed", "count": 3, "score": 3}
    assert client.post("/quiz/note", json=body, headers=SIGNED_OUT).status_code == 401
    r = client.post("/quiz/note", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["messages"][0]["content"].startswith("**Quiz completed**")
    history = client.get(f"/sessions/{sid}/messages", params={}).json()
    assert history["messages"][-1]["content"].startswith("**Quiz completed**")


def test_general_quiz_on_my_notes_reads_the_materials(chroma_store, embedder):
    svc, sid, llm, _ = _malai(chroma_store, embedder, json.dumps({"questions": [_mcq("pdf_in")]}))
    req = QuizRequest(session_id=sid, current_time_ms=0, count=1, general=True,
                      prompt="quiz me on my notes")
    resp = asyncio.run(svc.make_quiz(req))
    assert resp.questions[0].citation.chunk_id == "pdf_in"
    assert "<context>" in llm.prompts[0]
