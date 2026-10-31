"""Several chats per session: separate histories, create/rename/delete. Fakes only."""

from __future__ import annotations

from tests.conftest import SIGNED_OUT
import pytest
from fastapi.testclient import TestClient

from app.chat import router as chat_router
from app.chat.schemas import ChatResponse
from app.chat.sessions import message_store, session_store
from app.config import settings
from app.main import app


class _EchoService:
    """Persists the turn like the real service does, and records what history
    it was given, so the test can see which chat the request landed in."""

    def __init__(self) -> None:
        self.seen_history: list[list[str]] = []

    async def chat(self, *, session_id, message, **_kw):
        self.seen_history.append(message_store.user_texts(session_id))
        message_store.append(session_id, "user", message, [])
        message_store.append(session_id, "assistant", f"echo: {message}", [])
        return ChatResponse(answer=f"echo: {message}")


@pytest.fixture
def api():
    chat_router.reset_chat_state()
    svc = _EchoService()
    chat_router.set_service_override(svc)
    session = session_store.create(
        learner_id="l1", course_id="c1", resource_id="r1", lesson_id=None,
        resource_scope=["r1"], current_time_ms=0,
    )
    token = ""
    client = TestClient(app)
    yield client, session.id, token, svc
    chat_router.reset_chat_state()


def _say(client, sid, token, text, thread_id=None):
    body = {"session_id": sid, "current_time_ms": 0, "message": text}
    if thread_id:
        body["thread_id"] = thread_id
    r = client.post("/chat", json=body)
    assert r.status_code == 200, r.text
    return r


def _messages(client, sid, token, thread_id=None):
    params = {}
    if thread_id:
        params["thread_id"] = thread_id
    r = client.get(f"/sessions/{sid}/messages", params=params)
    assert r.status_code == 200, r.text
    return [m["content"] for m in r.json()["messages"]]


def test_a_new_chat_has_its_own_history(api):
    client, sid, token, svc = api
    _say(client, sid, token, "first question")
    new = client.post(f"/sessions/{sid}/threads", params={}).json()
    _say(client, sid, token, "fresh topic", thread_id=new["id"])

    assert _messages(client, sid, token) == ["first question", "echo: first question"]
    assert _messages(client, sid, token, new["id"]) == ["fresh topic", "echo: fresh topic"]
    # The new chat's first turn saw no earlier questions: context is per chat.
    assert svc.seen_history[-1] == []


def test_list_titles_from_first_question_and_rename(api):
    client, sid, token, _ = api
    new = client.post(f"/sessions/{sid}/threads", params={}).json()
    _say(client, sid, token, "what is a partial derivative", thread_id=new["id"])
    threads = client.get(f"/sessions/{sid}/threads", params={}).json()["threads"]
    by_id = {t["id"]: t for t in threads}
    assert set(by_id) == {"main", new["id"]}
    assert by_id[new["id"]]["title"] == "what is a partial derivative"
    assert by_id[new["id"]]["message_count"] == 2
    assert threads[0]["id"] == new["id"]  # most recently active first

    r = client.patch(
        f"/sessions/{sid}/threads/{new['id']}",
        params={}, json={"title": "  Exam   prep "},
    )
    assert r.status_code == 200 and r.json()["title"] == "Exam prep" and r.json()["renamed"]


def test_delete_removes_a_chat_and_empties_main(api):
    client, sid, token, _ = api
    _say(client, sid, token, "keep?")
    new = client.post(f"/sessions/{sid}/threads", params={}).json()
    _say(client, sid, token, "gone", thread_id=new["id"])

    assert client.delete(f"/sessions/{sid}/threads/{new['id']}", params={}).status_code == 200
    ids = [t["id"] for t in client.get(f"/sessions/{sid}/threads", params={}).json()["threads"]]
    assert ids == ["main"]
    # A deleted chat cannot be written to or read.
    r = client.post("/chat", json={
        "session_id": sid, "current_time_ms": 0,
        "message": "hello?", "thread_id": new["id"],
    })
    assert r.status_code == 404

    r = client.delete(f"/sessions/{sid}/threads/main", params={})
    assert r.json()["emptied"] is True
    assert _messages(client, sid, token) == []


def test_thread_routes_require_sign_in(api):
    client, sid, _token, _ = api
    assert client.get(f"/sessions/{sid}/threads", headers=SIGNED_OUT).status_code == 401
    assert client.post(f"/sessions/{sid}/threads", headers=SIGNED_OUT).status_code == 401


def test_unknown_or_malformed_thread_is_404(api):
    client, sid, token, _ = api
    for tid in ("thr_doesnotexist", "../main"):
        r = client.get(f"/sessions/{sid}/messages", params={"thread_id": tid})
        assert r.status_code == 404
