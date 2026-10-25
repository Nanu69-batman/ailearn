"""A Malai chat in no subject (``general=True``) answers without retrieval.

The learner's materials are searched only when the message explicitly points
at them ("what do my notes say ..."). Otherwise the turn is answered from
general knowledge with zero citations, and retrieval is never constructed.
"""

from __future__ import annotations

import asyncio

import pytest

from app.chat.intent import refers_to_materials
from app.chat.llm import LLMResult
from app.chat.prompts import FORMAT_RULE, GENERAL_SYSTEM
from app.chat.service import ChatService
from app.chat.sessions import InMemoryMessageStore, InMemorySessionStore


class RecordingLLM:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls: list[dict] = []

    async def complete(self, *, system, user_prompt, passages, mode):
        self.calls.append({"system": system, "user_prompt": user_prompt, "passages": list(passages)})
        return LLMResult(text=self.text)


class RetrievalUsed(AssertionError):
    pass


def _service(llm, monkeypatch):
    sessions = InMemorySessionStore()
    session = sessions.create(
        learner_id="l1",
        course_id="c1",
        resource_id="r1",
        lesson_id=None,
        resource_scope=["r1"],
        current_time_ms=0,
    )
    service = ChatService(store=object(), llm=llm, sessions=sessions, messages=InMemoryMessageStore())

    def no_retrieval(self):
        raise RetrievalUsed("a general turn must not retrieve")

    monkeypatch.setattr(ChatService, "_retrieval", no_retrieval)
    return service, session


def test_general_turn_skips_retrieval_and_cites_nothing(monkeypatch):
    llm = RecordingLLM("Bernoulli's equation [chunk_id=abc123] says energy is conserved.")
    service, session = _service(llm, monkeypatch)

    resp = asyncio.run(
        service.chat(
            session_id=session.id,
            current_time_ms=0,
            message="what is bernoulli's equation?",
            general=True,
        )
    )

    assert resp.mode == "general"
    assert resp.citations == []
    assert resp.grounded is False and resp.refused is False
    assert "[chunk_id=" not in resp.answer
    assert llm.calls[0]["system"] == GENERAL_SYSTEM
    assert llm.calls[0]["passages"] == []


def test_general_turn_carries_the_conversation(monkeypatch):
    llm = RecordingLLM("Sure.")
    service, session = _service(llm, monkeypatch)
    for q in ("what is a derivative?", "and a partial one?"):
        asyncio.run(service.chat(session_id=session.id, current_time_ms=0, message=q, general=True))

    prompt = llm.calls[1]["user_prompt"]
    assert "Learner: what is a derivative?" in prompt
    assert "Malai: Sure." in prompt
    assert prompt.rstrip().endswith("Learner: and a partial one?")


def test_explicit_material_reference_takes_the_lesson_path(monkeypatch):
    llm = RecordingLLM("unused")
    service, session = _service(llm, monkeypatch)
    # Pointing at the materials leaves the general path: the lesson pipeline
    # runs, which here means it reaches retrieval.
    with pytest.raises(RetrievalUsed):
        asyncio.run(
            service.chat(
                session_id=session.id,
                current_time_ms=0,
                message="what do my notes say about bernoulli's equation?",
                general=True,
            )
        )


@pytest.mark.parametrize(
    "text",
    [
        "what do my notes say about bernoulli",
        "explain the second lecture",
        "according to the slides, what is p?",
        "summarize the uploaded pdf",
        "what does this course cover",
    ],
)
def test_refers_to_materials(text):
    assert refers_to_materials(text)


@pytest.mark.parametrize(
    "text",
    [
        "what is bernoulli's equation",
        "explain this concept simply",
        "what is the best course of action here",
        "give me the derivation",
    ],
)
def test_does_not_refer_to_materials(text):
    assert not refers_to_materials(text)


def test_general_prompt_asks_for_markdown():
    assert FORMAT_RULE in GENERAL_SYSTEM
