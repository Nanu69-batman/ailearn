"""/chat/attachments: images as chat context, never resources (app/chat/attachments.py)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.chat import attachments
from app.chat import router as chat_router
from app.chat.launch import create_session_token
from app.chat.sessions import session_store
from app.config import settings
from app.main import app

PNG = b"\x89PNG\r\n\x1a\nfake"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "supabase_url", "")
    monkeypatch.setattr(settings, "supabase_service_role_key", "")
    monkeypatch.setattr(attachments, "_describe", lambda data, mime: "$x^2 + 2x = 0$")
    chat_router.reset_chat_state()
    attachments.reset()
    yield TestClient(app)
    attachments.reset()
    chat_router.reset_chat_state()


def _session():
    s = session_store.create(
        learner_id="l1",
        course_id="c",
        resource_id="r",
        lesson_id=None,
        resource_scope=["r"],
        current_time_ms=0,
    )
    return s, create_session_token(settings.lms_launch_secret, s.id, s.learner_id)


def _upload(client, sid, token, data=PNG, mime="image/png"):
    return client.post(
        "/chat/attachments",
        data={"session_id": sid, "session_token": token},
        files={"image": ("problem.png", data, mime)},
    )


def test_attach_transcribes_and_feeds_the_prompt(client):
    s, token = _session()
    r = _upload(client, s.id, token)
    assert r.status_code == 200, r.text
    assert r.json()["text"] == "$x^2 + 2x = 0$"
    note = attachments.prompt_note(s.id, "main")
    assert "problem.png" in note and "$x^2 + 2x = 0$" in note


def test_wrong_token_is_refused(client):
    s, _ = _session()
    assert _upload(client, s.id, "nope").status_code == 401


def test_non_image_is_refused(client):
    s, token = _session()
    assert _upload(client, s.id, token, b"%PDF", "application/pdf").status_code == 422


def test_delete_removes_it_from_the_prompt(client):
    s, token = _session()
    att = _upload(client, s.id, token).json()
    r = client.delete(
        f"/chat/attachments/{att['id']}",
        params={"session_id": s.id},
        headers={"x-session-token": token},
    )
    assert r.status_code == 204
    assert attachments.prompt_note(s.id, "main") == ""


def test_other_chats_do_not_see_it(client):
    s, token = _session()
    _upload(client, s.id, token)
    other, _ = _session()
    assert attachments.prompt_note(other.id, "main") == ""


def test_image_file_goes_to_storage(client, monkeypatch):
    import httpx

    from app.supa import client as supa

    seen: list[tuple[str, str]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.method, req.url.path))
        return httpx.Response(200, json=[])

    supa.set_client(
        supa.SupabaseClient("https://x.supabase.co", "k", transport=httpx.MockTransport(handler))
    )
    try:
        s = session_store.create(
            learner_id="u1",
            course_id="c",
            resource_id="r",
            lesson_id=None,
            resource_scope=["r"],
            current_time_ms=0,
        )
        token = create_session_token(settings.lms_launch_secret, s.id, s.learner_id)
        assert _upload(client, s.id, token).status_code == 200
    finally:
        supa.set_client(None)
    uploads = [p for m, p in seen if m == "POST" and p.startswith("/storage/v1/object/")]
    assert len(uploads) == 1
    assert uploads[0].startswith(f"/storage/v1/object/{settings.supabase_storage_bucket}/public/chat/{s.id}/att_")
    assert uploads[0].endswith(".png")
