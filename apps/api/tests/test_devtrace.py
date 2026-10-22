"""The developer-view trace records each pipeline step and never breaks one."""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from app import devtrace
from app.chat.llm import DeterministicStubLLM
from app.chat.service import ChatService
from app.chat.sessions import InMemoryMessageStore, InMemorySessionStore
from app.main import app
from tests.store_helpers import make_chunk

T = 120_000
TEXT = "partial derivatives hold one variable constant"


def _ask(chroma_store, embedder):
    chroma_store.add([
        make_chunk("early", resource_id="res_open", end_ms=60_000, text=TEXT),
        make_chunk("late", resource_id="res_open", start_ms=800_000, end_ms=900_000, text=TEXT),
    ])
    sessions = InMemorySessionStore()
    service = ChatService(
        store=chroma_store, embedder=embedder, llm=DeterministicStubLLM(),
        sessions=sessions, messages=InMemoryMessageStore(),
    )
    session = sessions.create(
        learner_id="l1", course_id="course_1", resource_id="res_open", lesson_id=None,
        resource_scope=["res_open"], current_time_ms=T,
    )
    return asyncio.run(service.chat(
        session_id=session.id, current_time_ms=T,
        message="what is a partial derivative", focus_resource_id="res_open",
    ))


def test_a_chat_answer_emits_every_step_in_order(chroma_store, embedder):
    devtrace.clear()
    _ask(chroma_store, embedder)
    events = devtrace.events_after(0)
    steps = [e["step"] for e in events]
    for step in ("request", "scope", "route", "plan", "embed_query", "search", "gate",
                 "llm", "respond"):
        assert step in steps, step
    assert steps[0] == "request" and steps[-1] == "respond"
    assert len({e["run_id"] for e in events}) == 1
    gate = next(e for e in events if e["step"] == "gate")
    # The trace reports the gate; it never relaxes it.
    assert all(p["end_ms"] <= T for p in gate["data"]["passages"])
    assert "late" not in [p["chunk_id"] for p in gate["data"]["passages"]]


def test_trace_endpoint_returns_events_after_a_cursor(chroma_store, embedder):
    devtrace.clear()
    _ask(chroma_store, embedder)
    client = TestClient(app)
    body = client.get("/dev/trace").json()
    assert body["enabled"] is True
    assert body["events"] and body["latest"] == body["events"][-1]["seq"]
    assert client.get(f"/dev/trace?after={body['latest']}").json()["events"] == []


def test_emit_outside_a_run_is_a_no_op():
    devtrace.clear()
    devtrace.emit("search", "end", data={"x": 1})
    assert devtrace.events_after(0) == []


def test_an_ingest_job_is_one_run_from_request_to_respond():
    from app.ingestion.jobs import new_job, run_ingest

    devtrace.clear()
    job = new_job("course_1", "res_x")
    job.trace = {"file": "notes.pdf", "source_type": "pdf"}
    run_ingest(job, lambda: {"total_chunks": 3})
    events = devtrace.events_after(0)
    assert [e["step"] for e in events] == ["request", "respond"]
    assert {e["run_id"] for e in events} == {job.job_id}
    assert events[0]["data"]["file"] == "notes.pdf"
    assert events[-1]["data"]["total_chunks"] == 3
