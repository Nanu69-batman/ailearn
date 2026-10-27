"""/malai/chats: the per-user chat list (app/chat/chats.py). No network."""

from __future__ import annotations

import time

import jwt
import pytest
from fastapi.testclient import TestClient

from app.chat import chats
from app.chat import router as chat_router
from app.chat.sessions import session_store
from app.config import settings
from app.main import app

SECRET = "test-jwt-secret-at-least-32-bytes-long!!"
ALICE = "8b1f6c1e-0000-4000-8000-00000000000a"
BOB = "8b1f6c1e-0000-4000-8000-00000000000b"


def _auth(sub: str) -> dict:
    now = int(time.time())
    claims = {"sub": sub, "aud": "authenticated", "exp": now + 3600, "iat": now}
    return {"Authorization": f"Bearer {jwt.encode(claims, SECRET, algorithm='HS256')}"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "supabase_url", "")
    monkeypatch.setattr(settings, "supabase_service_role_key", "")
    monkeypatch.setattr(settings, "supabase_jwt_secret", SECRET)
    monkeypatch.setattr(settings, "auth_required", False)
    chat_router.reset_chat_state()
    chats.reset_local()
    yield TestClient(app)
    chats.reset_local()
    chat_router.reset_chat_state()


def _session(owner: str | None):
    return session_store.create(
        learner_id=owner or "anon",
        course_id="c",
        resource_id="r",
        lesson_id=None,
        resource_scope=["r"],
        current_time_ms=0,
        owner_id=owner,
    )


def _row(session_id: str, **kw) -> dict:
    return {
        "session_id": session_id,
        "thread_id": "main",
        "title": "Tensors",
        "subject_id": "sub_1",
        "preview": "what is a tensor",
        "created_ms": 1,
        "updated_ms": 2,
        **kw,
    }


def test_anonymous_gets_401(client):
    assert client.get("/malai/chats").status_code == 401


def test_put_then_list_mints_a_token(client):
    s = _session(ALICE)
    r = client.put("/malai/chats/chat-1", json=_row(s.id), headers=_auth(ALICE))
    assert r.status_code == 200, r.text
    out = client.get("/malai/chats", headers=_auth(ALICE)).json()["chats"]
    assert [c["id"] for c in out] == ["chat-1"]
    assert out[0]["title"] == "Tensors"
    assert out[0]["session_token"]


def test_lists_are_per_user(client):
    s = _session(ALICE)
    client.put("/malai/chats/chat-1", json=_row(s.id), headers=_auth(ALICE))
    assert client.get("/malai/chats", headers=_auth(BOB)).json()["chats"] == []


def test_cannot_file_someone_elses_session(client):
    s = _session(ALICE)
    r = client.put("/malai/chats/chat-1", json=_row(s.id), headers=_auth(BOB))
    assert r.status_code == 404


def test_delete(client):
    s = _session(ALICE)
    client.put("/malai/chats/chat-1", json=_row(s.id), headers=_auth(ALICE))
    assert client.delete("/malai/chats/chat-1", headers=_auth(ALICE)).status_code == 204
    assert client.get("/malai/chats", headers=_auth(ALICE)).json()["chats"] == []


def test_newest_first(client):
    s = _session(ALICE)
    client.put("/malai/chats/old", json=_row(s.id, updated_ms=5), headers=_auth(ALICE))
    client.put("/malai/chats/new", json=_row(s.id, updated_ms=9), headers=_auth(ALICE))
    out = client.get("/malai/chats", headers=_auth(ALICE)).json()["chats"]
    assert [c["id"] for c in out] == ["new", "old"]
