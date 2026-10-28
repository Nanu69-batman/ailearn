"""Model modes, BYOK and OpenRouter fallback (app.chat.models, LessonAgent._post)."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.chat import models as m
from app.chat.agent import AgentUnavailableError, LessonAgent, get_agent

OK = {"choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}]}


def test_auto_is_the_free_router():
    assert m.models_for("auto", byok=False) == [m.FREE_ROUTER]
    assert m.models_for("auto", byok=True) == [m.FREE_ROUTER]


@pytest.mark.parametrize("mode", ["fast", "balanced", "powerful"])
def test_modes_without_a_key_use_free_models_only(mode):
    models = m.models_for(mode, byok=False)
    assert all(x.endswith(":free") or x == m.FREE_ROUTER for x in models)
    assert models[-1] == m.FREE_ROUTER


@pytest.mark.parametrize("mode", ["fast", "balanced", "powerful"])
def test_own_key_tries_paid_models_first_then_free(mode):
    models = m.models_for(mode, byok=True)
    assert models[: len(m.BYOK_MODELS[mode])] == m.BYOK_MODELS[mode]
    assert models[-1] == m.FREE_ROUTER
    assert len(models) == len(set(models))


def test_unknown_mode_and_bad_keys_are_ignored():
    assert m.normalize_mode("ULTRA") == "auto"
    assert m.normalize_mode(" Fast ") == "fast"
    assert m.normalize_key("x" * 500) == ""
    assert m.normalize_key("sk-or-v1-abc\n") == "sk-or-v1-abc"
    assert m.normalize_key("sk-or-é") == ""


def test_middleware_reads_the_headers_per_request():
    app = FastAPI()

    @app.get("/c")
    def read():
        c = m.current_choice()
        return {"mode": c.mode, "key": bool(c.byok_key)}

    app.add_middleware(m.ModelChoiceMiddleware)
    client = TestClient(app)
    assert client.get("/c", headers={"X-Model-Mode": "powerful", "X-OpenRouter-Key": "sk-or-1"}).json() == {
        "mode": "powerful",
        "key": True,
    }
    assert client.get("/c").json() == {"mode": "auto", "key": False}


def test_get_agent_prefers_the_learners_key(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "openrouter_api_key", "")
    assert get_agent() is None
    token = m.set_choice(m.ModelChoice(mode="balanced", byok_key="sk-or-mine"))
    try:
        agent = get_agent()
        assert agent is not None
        assert agent._api_key == "sk-or-mine"
        assert agent.model == m.BYOK_MODELS["balanced"][0]
    finally:
        m.reset_choice(token)


def _agent(handler, models):
    return LessonAgent(api_key="k", model=models, http_transport=httpx.MockTransport(handler))


def test_falls_back_to_the_next_model_and_sticks_with_it():
    tried = []

    def handler(request):
        model = json.loads(request.content)["model"]
        tried.append(model)
        if model == "a":
            return httpx.Response(429, json={"error": {"message": "rate limited"}})
        return httpx.Response(200, json=OK)

    agent = _agent(handler, ["a", "b", "c"])
    assert asyncio.run(agent._post({"messages": []}))["choices"]
    assert tried == ["a", "b"]
    assert agent.model == "b"
    # The next call in the same turn starts at the model that answered.
    asyncio.run(agent._post({"messages": []}))
    assert tried == ["a", "b", "b"]


def test_an_error_body_counts_as_a_failure():
    def handler(request):
        if json.loads(request.content)["model"] == "a":
            return httpx.Response(200, json={"error": {"code": 502}})
        return httpx.Response(200, json=OK)

    agent = _agent(handler, ["a", "b"])
    asyncio.run(agent._post({"messages": []}))
    assert agent.model == "b"


def test_a_rejected_key_stops_at_once():
    tried = []

    def handler(request):
        tried.append(json.loads(request.content)["model"])
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    with pytest.raises(AgentUnavailableError, match="401"):
        asyncio.run(_agent(handler, ["a", "b"])._post({"messages": []}))
    assert tried == ["a"]


def test_every_model_failing_raises():
    def handler(request):
        return httpx.Response(503)

    with pytest.raises(AgentUnavailableError, match="tried 2 models"):
        asyncio.run(_agent(handler, ["a", "b"])._post({"messages": []}))
