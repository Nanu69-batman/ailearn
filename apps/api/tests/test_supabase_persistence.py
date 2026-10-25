"""Write-through persistence + per-user ownership. No network (MockTransport)."""

from __future__ import annotations

import json
import time

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient

from app.chat import router as chat_router
from app.chat.sessions import (
    load_sessions_from_supabase,
    message_store,
    session_store,
    thread_store,
)
from app.config import settings
from app.main import app
from app.subjects.store import (
    load_subjects_from_supabase,
    reset_store,
    seed_demo_subjects_to_supabase,
    subject_store,
)
from app.supa.client import SupabaseClient, set_client

SECRET = "test-jwt-secret-at-least-32-bytes-long!!"
USER_A = "8b1f6c1e-0000-4000-8000-00000000000a"
USER_B = "8b1f6c1e-0000-4000-8000-00000000000b"


def _bearer(sub: str) -> dict:
    now = int(time.time())
    token = jwt.encode(
        {"sub": sub, "aud": "authenticated", "exp": now + 3600, "iat": now},
        SECRET,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


class FakeSupabase:
    """Records PostgREST calls; serves canned rows for GET; can fail on demand."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict, object]] = []
        self.rows: dict[str, list[dict]] = {}
        self.fail = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.fail:
            return httpx.Response(500, text="boom")
        table = request.url.path.rsplit("/", 1)[-1]
        body = json.loads(request.content) if request.content else None
        params = dict(request.url.params)
        self.calls.append((request.method, table, params, body))
        if request.method == "GET":
            offset = int(params.get("offset", 0))
            return httpx.Response(200, json=self.rows.get(table, [])[offset:])
        return httpx.Response(204)

    def writes(self, method: str, table: str) -> list:
        return [c for c in self.calls if c[0] == method and c[1] == table]


@pytest.fixture
def fake():
    f = FakeSupabase()
    set_client(
        SupabaseClient(
            "https://x.supabase.co", "svc", transport=httpx.MockTransport(f)
        )
    )
    yield f
    set_client(None)


@pytest.fixture
def client(monkeypatch, fake):
    monkeypatch.setattr(settings, "supabase_jwt_secret", SECRET)
    monkeypatch.setattr(settings, "auth_required", False)
    monkeypatch.setattr(settings, "allow_unsigned_launch", True)
    chat_router.reset_chat_state()
    reset_store()
    yield TestClient(app)
    chat_router.reset_chat_state()
    reset_store()


def _launch() -> dict:
    return {
        "learner_id": "body-learner",
        "course_id": "c1",
        "resource_id": "r1",
        "resource_scope": ["r1"],
        "current_time_ms": 0,
        "issued_at": int(time.time() * 1000),
    }


# --- subjects: mirroring ------------------------------------------------------


def test_subject_create_update_delete_are_mirrored(client, fake):
    r = client.post("/subjects", json={"name": "Physics"}, headers=_bearer(USER_A))
    assert r.status_code == 201
    sid = r.json()["id"]
    ups = fake.writes("POST", "subjects")
    assert len(ups) == 1
    row = ups[0][3][0]
    assert row["id"] == sid and row["owner_id"] == USER_A and row["name"] == "Physics"
    assert ups[0][2]["on_conflict"] == "id"

    client.patch(f"/subjects/{sid}", json={"name": "Chem"}, headers=_bearer(USER_A))
    ups = fake.writes("POST", "subjects")
    assert len(ups) == 2 and ups[1][3][0]["name"] == "Chem"

    r = client.delete(f"/subjects/{sid}", headers=_bearer(USER_A))
    assert r.status_code == 200
    dels = fake.writes("DELETE", "subjects")
    assert len(dels) == 1 and dels[0][2] == {"id": f"eq.{sid}"}


def test_no_supabase_calls_when_disabled(monkeypatch):
    set_client(None)
    monkeypatch.setattr(settings, "supabase_url", "")
    monkeypatch.setattr(settings, "supabase_service_role_key", "")
    reset_store()
    s = subject_store.create(name="x", owner_id=USER_A)
    assert s.owner_id == USER_A
    reset_store()


def test_supabase_errors_do_not_fail_requests(client, fake):
    fake.fail = True
    r = client.post("/subjects", json={"name": "Physics"}, headers=_bearer(USER_A))
    assert r.status_code == 201
    sid = r.json()["id"]
    assert client.patch(
        f"/subjects/{sid}", json={"name": "N"}, headers=_bearer(USER_A)
    ).status_code == 200
    assert client.delete(f"/subjects/{sid}", headers=_bearer(USER_A)).status_code == 200
    # sessions too
    r = client.post("/lms/session", json=_launch(), headers=_bearer(USER_A))
    assert r.status_code == 200
    message_store.append(r.json()["session_id"], "user", "hi")


# --- subjects: ownership ------------------------------------------------------


def test_subject_isolation_between_users(client):
    sid = client.post(
        "/subjects", json={"name": "A's"}, headers=_bearer(USER_A)
    ).json()["id"]

    ids = lambda h: [
        s["id"] for s in client.get("/subjects", headers=h).json()["subjects"]
    ]
    assert sid in ids(_bearer(USER_A))
    assert sid not in ids(_bearer(USER_B))
    assert sid not in ids({})

    hb = _bearer(USER_B)
    assert client.get(f"/subjects/{sid}", headers=hb).status_code == 404
    assert client.get(f"/subjects/{sid}/resources", headers=hb).status_code == 404
    assert client.patch(f"/subjects/{sid}", json={"name": "x"}, headers=hb).status_code == 404
    assert client.post(
        f"/subjects/{sid}/resources", json={"resource_id": "r1"}, headers=hb
    ).status_code == 404
    assert client.delete(f"/subjects/{sid}/resources/r1", headers=hb).status_code == 404
    assert client.delete(f"/subjects/{sid}", headers=hb).status_code == 404
    assert client.get(f"/subjects/{sid}").status_code == 404
    # still intact for the owner
    assert client.get(f"/subjects/{sid}", headers=_bearer(USER_A)).json()["name"] == "A's"


def test_public_subject_visible_and_mutable_unless_auth_required(client, monkeypatch):
    pub = subject_store.create(name="Public demo")  # owner None
    for h in ({}, _bearer(USER_A), _bearer(USER_B)):
        listed = client.get("/subjects", headers=h).json()["subjects"]
        assert pub.id in [s["id"] for s in listed]
        assert client.get(f"/subjects/{pub.id}", headers=h).status_code == 200

    # today's behaviour while auth is not required
    assert client.patch(
        f"/subjects/{pub.id}", json={"name": "ok"}, headers=_bearer(USER_A)
    ).status_code == 200

    monkeypatch.setattr(settings, "auth_required", True)
    assert client.patch(
        f"/subjects/{pub.id}", json={"name": "no"}, headers=_bearer(USER_A)
    ).status_code == 403
    assert client.delete(f"/subjects/{pub.id}", headers=_bearer(USER_A)).status_code == 403
    assert client.get(f"/subjects/{pub.id}").status_code == 200


# --- subjects: startup load ---------------------------------------------------


def test_subjects_load_from_supabase(client, fake):
    fake.rows["subjects"] = [
        {
            "id": "sub_1",
            "owner_id": USER_A,
            "name": "Mine",
            "course_id": "c1",
            "resource_ids": ["r1"],
            "created_at": "2020-01-01T00:00:00+00:00",  # old: no TTL for Supabase rows
        },
        {"id": "sub_2", "owner_id": None, "name": "Pub", "resource_ids": []},
    ]
    reset_store()
    assert load_subjects_from_supabase() == 2
    got = subject_store.get("sub_1")
    assert got and got.owner_id == USER_A and got.resource_ids == ["r1"]
    ids = [s["id"] for s in client.get("/subjects", headers=_bearer(USER_B)).json()["subjects"]]
    assert ids == ["sub_2"]


def test_demo_subjects_seeded_public_and_others_skipped(client, fake, tmp_path, monkeypatch):
    seed = tmp_path / "subjects.json"
    seed.write_text(json.dumps({"subjects": {
        "sub_demo": {"id": "sub_demo", "name": "Demo", "course_id": "demo",
                     "owner_id": USER_A, "resource_ids": ["r1"],
                     "created_at": "2020-01-01T00:00:00+00:00"},
        "sub_dev": {"id": "sub_dev", "name": "Dev's own", "course_id": "c1",
                    "resource_ids": []},
        "sub_have": {"id": "sub_have", "name": "Seed copy", "course_id": "demo",
                     "resource_ids": []},
    }}), encoding="utf-8")
    monkeypatch.setattr("app.subjects.store.SUBJECT_STATE_PATH", seed)
    fake.rows["subjects"] = [{"id": "sub_have", "owner_id": None, "name": "Already there",
                              "course_id": "demo", "resource_ids": []}]
    reset_store()
    load_subjects_from_supabase()
    assert seed_demo_subjects_to_supabase() == 1
    demo = subject_store.get("sub_demo")
    assert demo and demo.owner_id is None  # forced public, old date kept (no TTL)
    assert subject_store.get("sub_dev") is None  # non-demo rows never seeded
    assert subject_store.get("sub_have").name == "Already there"  # existing row untouched
    upserts = fake.writes("POST", "subjects")
    assert [b[0]["id"] for *_, b in upserts] == ["sub_demo"]
    ids = [s["id"] for s in client.get("/subjects").json()["subjects"]]
    assert "sub_demo" in ids


def test_subject_load_failure_returns_zero(client, fake):
    fake.fail = True
    assert load_subjects_from_supabase() == 0


# --- sessions / threads / messages --------------------------------------------


def test_session_thread_message_mirroring(client, fake):
    r = client.post("/lms/session", json=_launch(), headers=_bearer(USER_A))
    assert r.status_code == 200
    sid = r.json()["session_id"]
    assert r.json()["learner_id"] == USER_A
    srow = fake.writes("POST", "chat_sessions")[0][3][0]
    assert srow["id"] == sid and srow["owner_id"] == USER_A
    assert srow["data"]["learner_id"] == USER_A and srow["resource_scope"] == ["r1"]

    t = thread_store.create(sid)
    trow = fake.writes("POST", "chat_threads")[-1][3][0]
    assert trow["id"] == t.id and trow["session_id"] == sid and trow["owner_id"] == USER_A

    thread_store.rename(sid, t.id, "Renamed")
    assert fake.writes("POST", "chat_threads")[-1][3][0]["title"] == "Renamed"

    msg = message_store.append(sid, "user", "hello", [{"x": 1}])
    mrow = fake.writes("POST", "chat_messages")[-1][3][0]
    assert mrow["id"] == msg.id and mrow["thread_id"] == "main"
    assert mrow["owner_id"] == USER_A and mrow["citations"] == [{"x": 1}]
    assert mrow["data"]["content"] == "hello"

    thread_store.delete(sid, t.id)
    assert fake.writes("DELETE", "chat_threads")[-1][2] == {
        "session_id": f"eq.{sid}",
        "id": f"eq.{t.id}",
    }
    assert fake.writes("DELETE", "chat_messages")[-1][2]["thread_id"] == f"eq.{t.id}"


def test_anonymous_session_has_no_owner(client, fake):
    r = client.post("/lms/session", json=_launch())
    assert r.status_code == 200
    assert fake.writes("POST", "chat_sessions")[0][3][0]["owner_id"] is None


def test_session_owner_isolation(client):
    r = client.post("/lms/session", json=_launch(), headers=_bearer(USER_A))
    sid, tok = r.json()["session_id"], r.json()["session_token"]
    message_store.append(sid, "user", "secret")

    url = f"/sessions/{sid}/messages?session_token={tok}"
    assert client.get(url, headers=_bearer(USER_A)).status_code == 200
    # B has the token (worst case) but is not the owner; anonymous neither
    assert client.get(url, headers=_bearer(USER_B)).status_code == 404
    assert client.get(url).status_code == 404

    turl = f"/sessions/{sid}/threads?session_token={tok}"
    assert client.get(turl, headers=_bearer(USER_B)).status_code == 404
    assert client.post(turl, headers=_bearer(USER_B)).status_code == 404
    assert client.get(turl, headers=_bearer(USER_A)).status_code == 200

    chat = {
        "session_id": sid,
        "session_token": tok,
        "message": "hi",
        "current_time_ms": 0,
    }
    assert client.post("/chat", json=chat, headers=_bearer(USER_B)).status_code == 404
    assert client.post("/chat/stream", json=chat, headers=_bearer(USER_B)).status_code == 404


def test_sessions_load_from_supabase(client, fake):
    r = client.post("/lms/session", json=_launch(), headers=_bearer(USER_A))
    sid, tok = r.json()["session_id"], r.json()["session_token"]
    t = thread_store.create(sid)
    message_store.append(sid, "user", "main q")
    from app.chat.sessions import current_thread

    current_thread.set(t.id)
    try:
        message_store.append(sid, "assistant", "thread a", [{"c": 1}])
    finally:
        current_thread.set("main")

    # Turn the recorded writes into the rows Supabase would return.
    for table in ("chat_sessions", "chat_threads", "chat_messages"):
        fake.rows[table] = [c[3][0] for c in fake.writes("POST", table)]

    chat_router.reset_chat_state()
    assert session_store.get(sid) is None
    assert load_sessions_from_supabase() == 1

    loaded = session_store.get(sid)
    assert loaded and loaded.owner_id == USER_A and loaded.resource_scope == ["r1"]
    assert [m.content for m in message_store.list_for_session(sid, "main")] == ["main q"]
    thread_msgs = message_store.list_for_session(sid, t.id)
    assert [m.content for m in thread_msgs] == ["thread a"]
    assert thread_msgs[0].citations == [{"c": 1}]
    assert thread_store.get(sid, t.id) is not None

    url = f"/sessions/{sid}/messages?session_token={tok}"
    assert client.get(url, headers=_bearer(USER_A)).status_code == 200
    assert client.get(url, headers=_bearer(USER_B)).status_code == 404


def test_session_load_failure_returns_zero(client, fake):
    fake.fail = True
    assert load_sessions_from_supabase() == 0
