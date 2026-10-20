# ruff: noqa: F811
"""A spent daily quota fails fast with a readable reason. Fakes only."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.ingestion import backoff
from app.ingestion.backoff import QuotaExhaustedError
from tests.test_progressive_ingest import (  # noqa: F401  (env is a fixture)
    COURSE,
    RID,
    _run,
    env,
)

# Shape of the real google-genai error text (trimmed), as seen in the API log.
DAILY_429 = (
    "429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded "
    "your current quota... Please retry in 12h12m42.5s.', 'status': "
    "'RESOURCE_EXHAUSTED', 'details': [{'@type': "
    "'type.googleapis.com/google.rpc.QuotaFailure', 'violations': [{'quotaMetric': "
    "'generativelanguage.googleapis.com/embed_content_free_tier_requests', "
    "'quotaId': 'EmbedContentRequestsPerDayPerUserPerProjectPerModel-FreeTier', "
    "'quotaValue': '1000'}]}, {'@type': 'type.googleapis.com/google.rpc.RetryInfo', "
    "'retryDelay': '43962s'}]}}"
)
MINUTE_429 = (
    "429 RESOURCE_EXHAUSTED. {'error': {'details': [{'quotaId': "
    "'EmbedContentRequestsPerMinutePerProjectPerModel-FreeTier'}, "
    "{'retryDelay': '35s'}]}}"
)


def test_daily_quota_fails_on_first_attempt_with_readable_message():
    calls, sleeps = [], []

    def fn():
        calls.append(1)
        raise RuntimeError(DAILY_429)

    with pytest.raises(QuotaExhaustedError) as ei:
        backoff.call_with_retry(fn, what="embed", max_attempts=8, sleep=sleeps.append)
    assert len(calls) == 1 and sleeps == []
    msg = str(ei.value)
    assert "Gemini embedding quota reached" in msg
    assert "1000" in msg and "12h 12m" in msg
    assert "{" not in msg  # no raw provider payload in the job status
    assert backoff.active_quota_block() == msg


def test_per_minute_quota_still_retries():
    attempts = []

    def fn():
        attempts.append(1)
        if len(attempts) < 3:
            raise RuntimeError(MINUTE_429)
        return "ok"

    assert backoff.call_with_retry(fn, what="embed", sleep=lambda s: None) == "ok"
    assert len(attempts) == 3
    assert backoff.active_quota_block() is None


def test_quota_error_maps_to_503_with_message():
    from app.main import app

    @app.get("/__test_quota")
    def _boom():
        raise QuotaExhaustedError("Gemini embedding quota reached. It resets daily.")

    try:
        r = TestClient(app).get("/__test_quota")
        assert r.status_code == 503
        assert r.json()["detail"].startswith("Gemini embedding quota reached")
    finally:
        app.router.routes = [
            rt for rt in app.router.routes if getattr(rt, "path", "") != "/__test_quota"
        ]


def test_position_reports_quota_instead_of_queueing(env):
    client, store, uploads, router_mod = env
    router_mod.reset_inflight()
    (uploads / f"{RID}.mp4").write_bytes(b"x")
    _run(store, mode="stream", n_windows=1, stages=("transcript",))
    backoff.note_quota_block(3600, "Gemini embedding quota reached. It resets in about 1h 0m.")
    url = f"/courses/{COURSE}/resources/{RID}/position"
    b = client.post(url, json={"t_ms": 100_000}).json()
    assert b["status"] == "failed" and b["job_ids"] == []
    assert b["error"].startswith("Gemini embedding quota reached")
    backoff.clear_quota_block()
    b = client.post(url, json={"t_ms": 100_000}).json()
    assert b["status"] == "queued"


def test_chat_reports_spent_quota_as_503_not_502(monkeypatch):
    from app.chat import router as chat_router
    from app.chat.launch import create_session_token
    from app.config import settings
    from app.main import app

    class _Spent:
        async def chat(self, **kw):
            raise QuotaExhaustedError("Gemini embedding quota reached. It resets in about 2h 0m.")

    monkeypatch.setattr(chat_router, "get_service", lambda: _Spent())
    session = chat_router.session_store.create(
        learner_id="l1", course_id="c1", resource_id="r1", lesson_id=None,
        resource_scope=["r1"], current_time_ms=0,
    )
    token = create_session_token(settings.lms_launch_secret, session.id, "l1")
    r = TestClient(app).post("/chat", json={
        "session_id": session.id, "session_token": token,
        "current_time_ms": 0, "message": "hi",
    })
    assert r.status_code == 503
    assert r.json()["detail"].startswith("Gemini embedding quota reached")
