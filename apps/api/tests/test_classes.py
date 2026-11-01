"""Teacher classes: roles from the token, join by code, read-only class subjects."""

from __future__ import annotations

import time

import jwt
import pytest
from fastapi.testclient import TestClient

from app.classes import store as class_state
from app.config import settings
from app.main import app
from app.subjects import store as subject_state

SECRET = "test-jwt-secret-at-least-32-bytes-long!!"
TEACHER = "00000000-0000-4000-8000-0000000000a1"
STUDENT = "00000000-0000-4000-8000-0000000000b2"
OTHER = "00000000-0000-4000-8000-0000000000c3"

pytestmark = pytest.mark.real_auth


def _h(sub: str, role: str | None, name: str = "N") -> dict:
    now = int(time.time())
    claims = {"sub": sub, "aud": "authenticated", "exp": now + 3600, "iat": now,
              "email": f"{sub[-2:]}@x.y", "user_metadata": {"full_name": name}}
    if role:
        claims["user_metadata"]["role"] = role
    return {"Authorization": f"Bearer {jwt.encode(claims, SECRET, algorithm='HS256')}"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "supabase_url", "")
    monkeypatch.setattr(settings, "supabase_service_role_key", "")
    monkeypatch.setattr(settings, "supabase_jwt_secret", SECRET)
    monkeypatch.setattr(settings, "auth_required", True)
    class_state.reset_store()
    subject_state.reset_store()
    yield TestClient(app)
    class_state.reset_store()
    subject_state.reset_store()


def test_full_class_flow(client):
    t, s, o = _h(TEACHER, "teacher", "Ms T"), _h(STUDENT, "student", "Sam"), _h(OTHER, "student")

    # Only teachers create classes.
    assert client.post("/classes", json={"name": "X"}, headers=s).status_code == 403
    r = client.post("/classes", json={"name": "Physics 101"}, headers=t)
    assert r.status_code == 201
    klass = r.json()
    code, cid = klass["join_code"], klass["id"]
    assert klass["teacher_name"] == "Ms T"

    # Teacher adds a subject to the class.
    r = client.post("/subjects", json={"name": "Optics", "class_id": cid}, headers=t)
    assert r.status_code == 201
    sid = r.json()["id"]
    assert r.json()["class_id"] == cid

    # Before joining, the student cannot see it.
    assert client.get(f"/subjects/{sid}", headers=s).status_code == 404

    # Preview + join.
    p = client.get(f"/classes/join/{code.lower()}", headers=s).json()
    assert p["name"] == "Physics 101" and p["already_member"] is False
    assert client.post("/classes/join", json={"code": code}, headers=s).status_code == 200
    assert client.post("/classes/join", json={"code": code}, headers=t).status_code == 409

    # Student sees the subject read-only, cannot change it or create their own.
    r = client.get(f"/subjects/{sid}", headers=s)
    assert r.status_code == 200 and r.json()["can_edit"] is False
    assert any(x["id"] == sid for x in client.get("/subjects", headers=s).json()["subjects"])
    assert client.patch(f"/subjects/{sid}", json={"name": "z"}, headers=s).status_code == 403
    assert client.delete(f"/subjects/{sid}", headers=s).status_code == 403
    assert client.post("/subjects", json={"name": "mine"}, headers=s).status_code == 403

    # Student's class list; no join code or roster for students.
    mine = client.get("/classes", headers=s).json()["classes"]
    assert [c["id"] for c in mine] == [cid] and "join_code" not in mine[0]

    # Teacher sees the roster; outsiders get 404.
    roster = client.get(f"/classes/{cid}", headers=t).json()["students"]
    assert [m["id"] for m in roster] == [STUDENT] and roster[0]["name"] == "Sam"
    assert client.get(f"/classes/{cid}", headers=o).status_code == 404

    # New code invalidates the old link.
    new = client.post(f"/classes/{cid}/code", headers=t).json()["join_code"]
    assert new != code
    assert client.get(f"/classes/join/{code}", headers=o).status_code == 404

    # Teacher removes the student: access is gone.
    assert client.delete(f"/classes/{cid}/members/{STUDENT}", headers=t).status_code == 200
    assert client.get(f"/subjects/{sid}", headers=s).status_code == 404


def test_individual_cannot_join_and_keeps_own_subjects(client):
    t, i = _h(TEACHER, "teacher"), _h(OTHER, None)
    code = client.post("/classes", json={"name": "C"}, headers=t).json()["join_code"]
    assert client.post("/classes/join", json={"code": code}, headers=i).status_code == 403
    r = client.post("/subjects", json={"name": "Own"}, headers=i)
    assert r.status_code == 201 and r.json()["can_edit"] is True


def test_class_list_requires_sign_in(client):
    assert client.get("/classes").status_code == 401
