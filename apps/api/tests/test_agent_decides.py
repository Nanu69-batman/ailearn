"""A subject chat's agent decides what a message needs; no regex or cutoff does.

The model reads the materials list, searches what this chat may read (with
the learner's typos fixed by the model itself), searches the materials it may
not read yet, and ends the turn by answering, asking for access, or offering
general knowledge. The gates stay in code: nothing from a material outside
the chat can be cited, and asking is never saved as a turn.
"""

from __future__ import annotations

import asyncio

import pytest

from app.chat.agent import LessonAgent
from app.chat.llm import DeterministicStubLLM
from app.chat.service import ChatService
from app.chat.sessions import InMemoryMessageStore, InMemorySessionStore
from tests.store_helpers import make_chunk
from tests.test_agent import ScriptedTransport, StoreRetrieval, call, say

MATERIALS = [
    {"id": "res_notes", "title": "Pravin Chandra notes", "kind": "pdf"},
    {"id": "res_vid", "title": "Partial derivatives lecture", "kind": "video"},
]


@pytest.fixture
def env(chroma_store, monkeypatch):
    chroma_store.add([
        make_chunk("n1", resource_id="res_notes", source_type="pdf", start_ms=None, end_ms=None,
                   text="integrating factors make a form exact"),
        make_chunk("v1", resource_id="res_vid", start_ms=0, end_ms=60_000,
                   text="a partial derivative holds the other variable fixed"),
    ])

    def make(*replies, readable=("res_notes",)):
        transport = ScriptedTransport(*replies)
        # max_steps=2 is the production setting: deciding must still get room.
        agent = LessonAgent(api_key="test", model="m", max_steps=2, transport=transport)
        sessions = InMemorySessionStore()
        messages = InMemoryMessageStore()
        service = ChatService(
            store=chroma_store, embedder=None, llm=DeterministicStubLLM(),
            sessions=sessions, messages=messages, agent=agent,
        )
        monkeypatch.setattr(ChatService, "_retrieval", lambda self: StoreRetrieval(chroma_store))
        session = sessions.create(
            learner_id="l1", course_id="course_1", resource_id="res_notes",
            lesson_id="lesson_1", resource_scope=["res_notes", "res_vid"], current_time_ms=0,
        )

        def ask(message):
            return asyncio.run(service.chat(
                session_id=session.id, current_time_ms=0, message=message,
                scope_resource_ids=list(readable), completed_resource_ids=list(readable),
                ask_first=True, other_resource_ids=["res_vid"], materials=MATERIALS,
            ))

        return ask, transport, messages, session

    return make


def test_a_typo_still_reaches_the_material_that_covers_it(env):
    ask, transport, messages, session = env(
        call("search_materials", query="partial derivatives"),
        call("ask_learner", action="request_access", material_ids=["res_vid"],
             message="The lecture video covers partial derivatives. Give this chat access?"),
    )
    resp = ask("what are parital derivatives")
    assert resp.suggested_resource_ids == ["res_vid"]
    assert resp.answer.startswith("The lecture video covers")
    assert not resp.citations and not resp.refused
    # Asking is not a turn: a yes re-asks and saves it once.
    assert messages.list_for_session(session.id) == []
    # The model was given the materials list and the decision tools.
    first = transport.payloads[0]
    assert "Pravin Chandra notes" in first["messages"][0]["content"]
    assert {t["function"]["name"] for t in first["tools"]} >= {"search_materials", "ask_learner"}


def test_other_materials_are_for_deciding_never_citing(env):
    ask, _, _, _ = env(
        call("search_materials", query="partial derivatives"),
        say("A partial derivative holds the other variable fixed [chunk_id=v1]."),
    )
    # v1 lives in a material this chat cannot read: citing it is a defect,
    # and the agent falls back rather than serve it.
    resp = ask("what are partial derivatives")
    assert all(c.chunk_id != "v1" for c in resp.citations)


def test_not_covered_anywhere_offers_general_knowledge(env):
    ask, _, _, _ = env(
        call("search_materials", query="fourier series"),
        call("ask_learner", action="offer_general", message="Not in your materials. Answer from general knowledge?"),
    )
    resp = ask("explain fourier series")
    assert resp.offer_general and not resp.suggested_resource_ids


def test_a_greeting_is_answered_without_any_tool(env):
    ask, transport, _, _ = env(say("Hi! How can I help with your subject?"))
    resp = ask("heyy how r u")
    assert resp.mode == "conversational" and not resp.refused
    assert len(transport.payloads) == 1


def test_readable_material_answers_with_citations(env):
    ask, _, _, _ = env(
        call("search_materials", query="integrating factor", material_ids=["res_notes"]),
        say("An integrating factor makes the form exact [chunk_id=n1]."),
    )
    resp = ask("wat is an integratng factor in the notes")
    assert resp.mode == "grounded"
    assert [c.chunk_id for c in resp.citations] == ["n1"]


def test_nothing_ticked_still_lets_the_agent_decide(env):
    ask, _, _, _ = env(
        call("search_materials", query="integrating factor"),
        call("ask_learner", action="request_access", material_ids=["res_vid"], message="Allow the video?"),
        readable=(),
    )
    resp = ask("integrating factor?")
    assert not resp.scope_empty
    assert resp.suggested_resource_ids == ["res_vid"]


def test_one_search_returns_hidden_materials_marked_not_readable(env):
    ask, transport, _, _ = env(
        call("search_materials", query="partial derivatives"),
        call("ask_learner", action="request_access", material_ids=["res_vid"], message="Allow?"),
    )
    ask("what are parital derivatives")
    result = next(m["content"] for m in transport.payloads[1]["messages"] if m["role"] == "tool")
    readable, hidden = result.split("## Not readable in this chat yet")
    assert "n1" in readable or "Nothing matches" in readable
    assert 'material id="res_vid"' in hidden and "holds the other variable fixed" in hidden


def test_a_tool_call_written_as_text_is_still_made(env):
    ask, transport, _, _ = env(
        call("search_materials", query="partial derivatives"),
        say(
            "<tool_call>\n<function=ask_learner>\n<parameter=action>\nrequest_access\n</parameter>\n"
            '<parameter=material_ids>\n["res_vid"]\n</parameter>\n<parameter=message>\n'
            "Allow the video?\n</parameter>\n</function>\n</tool_call>"
        ),
    )
    resp = ask("its a partial derivatives question, search that video")
    assert resp.suggested_resource_ids == ["res_vid"]
    assert resp.answer == "Allow the video?"


def test_the_last_call_can_still_ask(env):
    ask, transport, _, _ = env(
        call("search_materials", query="pd"),
        call("search_materials", query="partial derivative"),
        call("search_materials", query="partial differentiation"),
        call("search_materials", query="derivative"),
        call("ask_learner", action="request_access", material_ids=["res_vid"], message="Allow?"),
    )
    resp = ask("pd?")
    assert resp.suggested_resource_ids == ["res_vid"]
    assert [t["function"]["name"] for t in transport.payloads[-1]["tools"]] == ["ask_learner"]


def test_general_chat_lets_the_agent_search_everything(chroma_store, monkeypatch):
    chroma_store.add([make_chunk("n1", resource_id="res_notes", source_type="pdf", start_ms=None,
                                 end_ms=None, text="integrating factors make a form exact")])
    transport = ScriptedTransport(
        call("search_materials", query="integrating factor"),
        say("It makes the form exact [chunk_id=n1]."),
    )
    agent = LessonAgent(api_key="test", model="m", max_steps=2, transport=transport)
    sessions = InMemorySessionStore()
    service = ChatService(store=chroma_store, embedder=None, llm=DeterministicStubLLM(),
                          sessions=sessions, messages=InMemoryMessageStore(), agent=agent)
    monkeypatch.setattr(ChatService, "_retrieval", lambda self: StoreRetrieval(chroma_store))
    session = sessions.create(learner_id="l1", course_id="course_1", resource_id="res_notes",
                              lesson_id="l", resource_scope=["res_notes"], current_time_ms=0)
    # No "my notes" wording: no regex decided this, the agent searched.
    resp = asyncio.run(service.chat(session_id=session.id, current_time_ms=0,
                                    message="wat is an integratng factor", general=True))
    assert resp.mode == "grounded" and [c.chunk_id for c in resp.citations] == ["n1"]
