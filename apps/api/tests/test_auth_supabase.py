"""Supabase access-token auth (docs/SUPABASE_PLAN.md, "Auth contract"). No network."""

from __future__ import annotations

import time

import jwt
import pytest
from fastapi.testclient import TestClient

from app.chat import router as chat_router
from app.config import settings
from app.main import app

SECRET = "test-jwt-secret-at-least-32-bytes-long!!"
USER_ID = "8b1f6c1e-0000-4000-8000-000000000001"


def _token(*, secret=SECRET, aud="authenticated", exp_in=3600, sub=USER_ID, email="a@b.c"):
    now = int(time.time())
    claims = {"sub": sub, "aud": aud, "exp": now + exp_in, "iat": now, "role": "authenticated"}
    if email:
        claims["email"] = email
    return jwt.encode(claims, secret, algorithm="HS256")


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "supabase_url", "")
    monkeypatch.setattr(settings, "supabase_service_role_key", "")
    monkeypatch.setattr(settings, "supabase_jwt_secret", SECRET)
    monkeypatch.setattr(settings, "auth_required", False)
    monkeypatch.setattr(settings, "allow_unsigned_launch", True)
    chat_router.reset_chat_state()
    yield TestClient(app)
    chat_router.reset_chat_state()


def _launch(course_id: str = "c1", learner_id: str = "body-learner") -> dict:
    return {
        "learner_id": learner_id,
        "course_id": course_id,
        "resource_id": "r1",
        "resource_scope": ["r1"],
        "current_time_ms": 0,
        "issued_at": int(time.time() * 1000),
    }


# --- get_current_user / require_user via a gated route -----------------------
# DELETE /subjects/<missing> is gated and side-effect free: 404 once past auth.


def test_valid_hs256_token_passes_the_gate(client, monkeypatch):
    monkeypatch.setattr(settings, "auth_required", True)
    r = client.delete("/subjects/sub_missing", headers=_bearer(_token()))
    assert r.status_code == 404


def test_expired_token_is_401_even_when_auth_not_required(client):
    r = client.delete("/subjects/sub_missing", headers=_bearer(_token(exp_in=-3600)))
    assert r.status_code == 401


def test_expired_within_leeway_is_accepted(client):
    r = client.delete("/subjects/sub_missing", headers=_bearer(_token(exp_in=-10)))
    assert r.status_code == 404


def test_wrong_audience_is_401(client):
    r = client.delete("/subjects/sub_missing", headers=_bearer(_token(aud="anon")))
    assert r.status_code == 401


def test_wrong_secret_is_401(client):
    bad = _token(secret="another-secret-that-is-32-bytes-long!!")
    r = client.delete("/subjects/sub_missing", headers=_bearer(bad))
    assert r.status_code == 401


def test_garbage_token_is_401(client):
    r = client.delete("/subjects/sub_missing", headers=_bearer("not.a.jwt"))
    assert r.status_code == 401


def test_no_header_anonymous_allowed_when_auth_not_required(client):
    r = client.delete("/subjects/sub_missing")
    assert r.status_code == 404


def test_no_header_is_401_when_auth_required(client, monkeypatch):
    monkeypatch.setattr(settings, "auth_required", True)
    r = client.delete("/subjects/sub_missing")
    assert r.status_code == 401


def test_ingest_routes_are_gated(client, monkeypatch):
    monkeypatch.setattr(settings, "auth_required", True)
    r = client.post("/courses/c1/ingest-youtube", json={"url": "https://youtu.be/x"})
    assert r.status_code == 401
    r = client.post("/courses/c1/ingest")
    assert r.status_code == 401


def test_read_routes_stay_open_when_auth_required(client, monkeypatch):
    monkeypatch.setattr(settings, "auth_required", True)
    assert client.get("/subjects").status_code == 200


def test_token_ignored_when_supabase_not_configured(client, monkeypatch):
    monkeypatch.setattr(settings, "supabase_jwt_secret", "")
    r = client.delete("/subjects/sub_missing", headers=_bearer("anything"))
    assert r.status_code == 404


# --- /lms/session ------------------------------------------------------------


def test_lms_session_forces_learner_id_to_user(client):
    r = client.post("/lms/session", json=_launch(), headers=_bearer(_token()))
    assert r.status_code == 200, r.text
    assert r.json()["learner_id"] == USER_ID


def test_lms_session_anonymous_keeps_body_learner(client):
    r = client.post("/lms/session", json=_launch())
    assert r.status_code == 200, r.text
    assert r.json()["learner_id"] == "body-learner"


def test_lms_session_requires_login_for_non_demo_when_auth_required(client, monkeypatch):
    monkeypatch.setattr(settings, "auth_required", True)
    r = client.post("/lms/session", json=_launch(course_id="c1"))
    assert r.status_code == 401


def test_lms_session_demo_course_is_anonymous_under_auth_required(client, monkeypatch):
    monkeypatch.setattr(settings, "auth_required", True)
    r = client.post("/lms/session", json=_launch(course_id="demo"))
    assert r.status_code == 200, r.text
    assert r.json()["learner_id"] == "body-learner"


def test_lms_session_bad_token_is_401(client):
    r = client.post("/lms/session", json=_launch(), headers=_bearer(_token(exp_in=-3600)))
    assert r.status_code == 401


def test_supabase_enabled_needs_url_and_service_key(monkeypatch):
    monkeypatch.setattr(settings, "supabase_url", "https://x.supabase.co")
    monkeypatch.setattr(settings, "supabase_service_role_key", "")
    assert settings.supabase_enabled() is False
    monkeypatch.setattr(settings, "supabase_service_role_key", "k")
    assert settings.supabase_enabled() is True
