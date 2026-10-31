"""POST /chat/stream: live preview events, then the validated answer. Fakes only."""

from __future__ import annotations

from tests.conftest import SIGNED_OUT
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app.chat import router as chat_router
from app.chat import streaming
from app.chat.llm import DeterministicStubLLM
from app.chat.schemas import ChatResponse
from app.chat.sessions import session_store
from app.config import settings
from app.main import app


class _StreamingService:
    """Streams a reply in pieces, splitting a citation marker across two."""

    def __init__(self, fail: Exception | None = None) -> None:
        self.fail = fail

    async def chat(self, **_kw):
        streaming.status("Searching the lesson")
        sink = streaming.current()
        assert sink is not None, "the route must bind a sink for the service"
        sink.reset()
        for piece in ["A partial derivative ", "holds y fixed [chu", "nk_id=res_a_c1]."]:
            sink.delta(piece)
            await asyncio.sleep(0)
        if self.fail is not None:
            raise self.fail
        return ChatResponse(answer="A partial derivative holds y fixed.", grounded=True, mode="grounded")


@pytest.fixture
def api():
    chat_router.reset_chat_state()
    session = session_store.create(
        learner_id="l1", course_id="c1", resource_id="r1", lesson_id=None,
        resource_scope=["r1"], current_time_ms=0,
    )
    token = ""
    yield TestClient(app), session.id, token
    chat_router.reset_chat_state()


def _events(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def _stream(client, sid, token):
    body = {"session_id": sid, "current_time_ms": 0, "message": "q"}
    return client.post("/chat/stream", json=body)


def test_stream_previews_text_without_markers_then_sends_the_answer(api):
    client, sid, token = api
    chat_router.set_service_override(_StreamingService())
    r = _stream(client, sid, token)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    events = _events(r.text)

    statuses = [d["text"] for k, d in events if k == "status"]
    assert statuses[:2] == ["Thinking", "Searching the lesson"]
    texts = [d["text"] for k, d in events if k == "text"]
    # An unclosed "[chu" is held back, and a closed marker never shows.
    assert texts == ["", "A partial derivative", "A partial derivative holds y fixed",
                     "A partial derivative holds y fixed."]
    assert all("chunk_id" not in t and "[" not in t for t in texts)
    kind, final = events[-1]
    assert kind == "done"
    assert final["answer"] == "A partial derivative holds y fixed."
    assert final["grounded"] is True


def test_stream_reports_provider_failure_as_an_error_event(api):
    client, sid, token = api
    chat_router.set_service_override(_StreamingService(fail=RuntimeError("boom")))
    events = _events(_stream(client, sid, token).text)
    assert events[-1] == ("error", {"status": 502, "detail": "upstream provider error"})
    assert all(k != "done" for k, _ in events)


def test_stream_rejects_signed_out_before_streaming(api):
    client, sid, _ = api
    chat_router.set_service_override(_StreamingService())
    body = {"session_id": sid, "current_time_ms": 0, "message": "q"}
    r = client.post("/chat/stream", json=body, headers=SIGNED_OUT)
    assert r.status_code == 401


def test_non_streaming_provider_still_previews_its_reply():
    """The stub (and a cache hit) emit their whole reply as one preview."""

    class _P:
        chunk_id = "res_a_c1"
        text = "Hold y fixed."

    async def run():
        sink = streaming.StreamSink()
        bound = streaming.bind(sink)
        try:
            await DeterministicStubLLM().complete(
                system="s", user_prompt="u", passages=[_P()], mode="qa"
            )
        finally:
            streaming.unbind(bound)
        await asyncio.sleep(0)
        items = []
        while not sink.queue.empty():
            items.append(sink.queue.get_nowait())
        return items

    items = asyncio.run(run())
    assert ("text", "") in items
    assert items[-1] == ("text", "Hold y fixed.")


def test_no_sink_means_no_streaming_side_effects():
    assert streaming.current() is None
    streaming.status("ignored")  # a no-op outside the streaming route
