"""Agentic chat: what the model may decide, and what it may never decide.

The model decides which tool to call and with what arguments. It never decides
what it is allowed to see: the tools clamp to the playhead and the granted
scope in code, and every cited id is validated against what the tools returned
in that turn. These tests drive :class:`app.chat.agent.LessonAgent` with a
scripted transport (no network) against the REAL Chroma store.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.chat.agent import AgentUnavailableError, LessonAgent
from app.chat.intent import (
    OVERVIEW,
    SMALLTALK,
    STUDY_QUESTIONS,
    classify_intent,
    has_subject_content,
)
from app.chat.llm import DeterministicStubLLM
from app.chat.service import ChatService
from app.chat.sessions import InMemoryMessageStore, InMemorySessionStore
from tests.store_helpers import make_chunk

T = 200_000  # the learner is paused at 3:20


def _corpus():
    return [
        make_chunk("c_early", start_ms=0, end_ms=60_000, text="a partial derivative holds y fixed"),
        make_chunk("c_mid", start_ms=60_000, end_ms=180_000, text="the notation uses a curly d"),
        make_chunk("c_edge", start_ms=180_000, end_ms=T, text="ends exactly at the playhead"),
        make_chunk("c_future", start_ms=190_000, end_ms=260_000, text="SPOILER it comes out to 2xy"),
        make_chunk("c_noend", start_ms=10_000, end_ms=None, text="SPOILER unknown end"),
        make_chunk("c_other", resource_id="res_other", start_ms=0, end_ms=5_000, text="SPOILER other resource"),
    ]


class ScriptedTransport:
    """Returns queued OpenRouter replies and records every payload sent."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.payloads: list[dict] = []

    async def __call__(self, payload):
        self.payloads.append(json.loads(json.dumps(payload)))
        # The last reply repeats: a model that keeps answering the same way.
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        return reply


def say(text):
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


def call(name, **args):
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(args)},
                        }
                    ],
                }
            }
        ]
    }


class StoreRetrieval:
    """The real store query, without the service wrapper's embedder plumbing."""

    def __init__(self, store):
        self.store = store
        self.calls = 0

    async def retrieve(self, query, course_id, resource_scope, current_time_ms,
                       k=8, focus_resource_id=None, prefer_recent=False):
        self.calls += 1
        return self.store.query(
            query, course_id=course_id, resource_scope=resource_scope,
            current_time_ms=current_time_ms, k=k, focus_resource_id=focus_resource_id,
        )


@pytest.fixture
def env(chroma_store, monkeypatch):
    chroma_store.add(_corpus())

    def make(*replies):
        transport = ScriptedTransport(*replies)
        agent = LessonAgent(api_key="test", model="m", max_steps=4, transport=transport)
        sessions = InMemorySessionStore()
        retrieval = StoreRetrieval(chroma_store)
        service = ChatService(
            store=chroma_store, embedder=None, llm=DeterministicStubLLM(),
            sessions=sessions, messages=InMemoryMessageStore(), agent=agent,
        )
        monkeypatch.setattr(ChatService, "_retrieval", lambda self: retrieval)
        session = sessions.create(
            learner_id="l1", course_id="course_1", resource_id="res_video",
            lesson_id="lesson_1", resource_scope=["res_video"], current_time_ms=T,
        )

        def ask(message, mode="qa"):
            return asyncio.run(service.chat(
                session_id=session.id, current_time_ms=T, message=message,
                mode=mode, focus_resource_id="res_video",
            ))

        return ask, transport, retrieval

    return make


def _tool_results(transport) -> str:
    return "\n".join(
        m["content"] for p in transport.payloads for m in p["messages"] if m["role"] == "tool"
    )


# --- the tools clamp, in code ----------------------------------------------


def test_range_tool_never_returns_past_the_playhead(chroma_store):
    chroma_store.add(_corpus())
    got = chroma_store.passages_between(
        course_id="course_1", resource_scope=["res_video"], current_time_ms=T,
        from_ms=0, to_ms=10_000_000,
    )
    ids = [p.chunk_id for p in got]
    assert ids == ["c_early", "c_mid", "c_edge"]  # lesson order, end_ms <= T


def test_range_tool_honours_a_narrow_window(chroma_store):
    chroma_store.add(_corpus())
    got = chroma_store.passages_between(
        course_id="course_1", resource_scope=["res_video"], current_time_ms=T,
        from_ms=100_000, to_ms=150_000,
    )
    # c_mid overlaps the window but ends at 180s > 150s: the window's end is a
    # gate too, so nothing past the requested moment is handed over.
    assert [p.chunk_id for p in got] == []
    got = chroma_store.passages_between(
        course_id="course_1", resource_scope=["res_video"], current_time_ms=T,
        from_ms=100_000, to_ms=T,
    )
    assert [p.chunk_id for p in got] == ["c_mid", "c_edge"]


def test_agent_asking_for_the_whole_video_gets_only_the_watched_part(env):
    ask, transport, _ = env(
        call("get_lesson_content", from_seconds=0, to_seconds=99_999),
        say("1. What does a partial derivative hold fixed? Answer: y [chunk_id=c_early]"),
    )
    resp = ask("what questions can prof ask me from this content")
    results = _tool_results(transport)
    assert "SPOILER" not in results
    assert "c_early" in results and "c_edge" in results
    assert resp.mode == "grounded" and resp.grounded
    assert [c.chunk_id for c in resp.citations] == ["c_early"]
    assert "[chunk_id=" not in resp.answer


# --- what the model decides ------------------------------------------------


def test_greeting_is_conversational_with_no_tools_and_no_citations(env):
    ask, transport, retrieval = env(say("Hi! I'm Malai, your AI learning assistant."))
    resp = ask("hi, i am an engineering student")
    assert resp.mode == "conversational"
    assert resp.citations == [] and not resp.refused
    assert retrieval.calls == 0
    assert len(transport.payloads) == 1


def test_who_are_you_is_conversational(env):
    ask, _, _ = env(say("I'm Malai, an AI learning assistant."))
    resp = ask("who are you")
    assert resp.mode == "conversational" and resp.citations == []


def test_labelled_general_knowledge_is_supplemental_and_uncited(env):
    ask, _, _ = env(
        call("search_lesson", query="chain rule"),
        say("The lesson hasn't covered this yet.\n\n**Not from the video** - general "
            "knowledge: the chain rule multiplies derivatives."),
    )
    resp = ask("what is the chain rule?")
    assert resp.mode == "supplemental" and resp.citations == [] and not resp.grounded


# --- what the model may never decide ---------------------------------------


def test_invented_citation_falls_back_to_the_classic_pipeline(env):
    ask, _, _ = env(
        call("search_lesson", query="partial derivative"),
        say("It is 2xy [chunk_id=c_future]"),  # never returned by a tool
    )
    resp = ask("what is a partial derivative?")
    assert "c_future" not in [c.chunk_id for c in resp.citations]
    assert "2xy" not in resp.answer


def test_uncited_subject_answer_without_tools_is_not_served(env):
    uncited = say("A partial derivative differentiates one variable at a time.")
    ask, transport, _ = env(uncited, uncited, uncited, uncited)
    resp = ask("what is a partial derivative?")
    # The agent first insisted on a tool call...
    assert transport.payloads[1]["tool_choice"] == "required"
    # ...and when the model still would not ground it, the classic pipeline
    # (stub LLM) answered instead, cited from the gate.
    assert resp.mode == "grounded"
    assert resp.citations and all(c.end_ms <= T for c in resp.citations)


def test_skipping_the_tools_gets_one_required_retry(env):
    ask, transport, _ = env(
        say("A partial derivative differentiates one variable at a time."),
        call("get_lesson_content", from_seconds=0, to_seconds=200),
        say("It holds y fixed [chunk_id=c_early]."),
    )
    resp = ask("what is a partial derivative?")
    assert transport.payloads[1]["tool_choice"] == "required"
    assert resp.mode == "grounded" and [c.chunk_id for c in resp.citations] == ["c_early"]


def test_uncited_answer_after_tool_use_gets_one_cite_repair(env):
    ask, transport, _ = env(
        call("get_lesson_content", from_seconds=0, to_seconds=200),
        say("The lesson hasn't reached the result yet."),
        say("The lesson hasn't reached the result yet; so far it holds y fixed [chunk_id=c_early]."),
    )
    resp = ask("what does it come out to at the end of the video?")
    assert "no citations" in transport.payloads[2]["messages"][-1]["content"]
    assert resp.mode == "grounded" and [c.chunk_id for c in resp.citations] == ["c_early"]


def test_provider_failure_falls_back_to_the_classic_pipeline(env):
    ask, _, _ = env(AgentUnavailableError("openrouter http 429"))
    resp = ask("what is a partial derivative?")
    assert resp.mode == "grounded"
    assert all(c.end_ms <= T for c in resp.citations)


def test_general_knowledge_is_not_served_for_a_quiz(env):
    ask, _, _ = env(say("**Not from the video** - general knowledge: Q1 ..."))
    resp = ask("quiz me", mode="quiz")
    assert resp.mode != "supplemental"


# --- intent classifier (classic-path routing) ------------------------------


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("hi", SMALLTALK),
        ("hi, i am an engineering student", SMALLTALK),
        ("who are you", SMALLTALK),
        ("thanks!", SMALLTALK),
        ("what can you do?", SMALLTALK),
        ("hi, what is a partial derivative?", None),
        ("i'm confused about partial derivatives", None),
        ("what is a partial derivative", None),
        ("what questions can prof ask me from this content", STUDY_QUESTIONS),
        ("quiz me", STUDY_QUESTIONS),
        ("what is this video about?", OVERVIEW),
        ("what have we covered so far", OVERVIEW),
    ],
)
def test_classify_intent(text, intent):
    assert classify_intent(text) == intent


def test_subject_content_gate():
    assert not has_subject_content("who are you")
    assert has_subject_content("explain the chain rule")
