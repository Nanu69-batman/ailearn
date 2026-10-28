"""Model modes, own provider keys and fallback (app.chat.models, LessonAgent._post)."""

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
    assert m.models_for("auto") == [m.FREE_ROUTER]


@pytest.mark.parametrize("mode", ["fast", "balanced", "powerful"])
def test_modes_use_free_models_only(mode):
    models = m.models_for(mode)
    assert all(x.endswith(":free") or x == m.FREE_ROUTER for x in models)
    assert models[-1] == m.FREE_ROUTER


def test_unknown_mode_and_bad_values_are_ignored():
    assert m.normalize_mode("ULTRA") == "auto"
    assert m.normalize_mode(" Fast ") == "fast"
    assert m.normalize_provider("nope") == ""
    assert m.normalize_key("x" * 500) == ""
    assert m.normalize_key("sk-abc\n") == "sk-abc"
    assert m.normalize_key("sk-é") == ""


def test_own_model_comes_first_then_the_free_fallback():
    choice = m.ModelChoice(mode="custom", provider="anthropic", key="sk-ant", model="claude-x")
    targets = m.targets_for(choice, "sk-or-server")
    assert targets[0] == m.Target("anthropic", "sk-ant", "claude-x")
    assert targets[0].url == "https://api.anthropic.com/v1/chat/completions"
    assert [t.model for t in targets[1:]] == [m.FREE_ROUTER]
    assert all(t.provider == "openrouter" for t in targets[1:])


def test_custom_without_a_model_is_just_auto():
    choice = m.ModelChoice(mode="custom", provider="openai", key="sk-1", model="")
    assert [t.model for t in m.targets_for(choice, "srv")] == [m.FREE_ROUTER]
    assert m.targets_for(choice, "") == []


def test_payload_is_adapted_per_provider():
    payload = {"messages": [], "max_tokens": 10, "temperature": 0.2, "reasoning": {"effort": "low"}}
    assert "reasoning" in m.adapt_payload(payload, m.Target("openrouter", "k", "a"))
    groq = m.adapt_payload(payload, m.Target("groq", "k", "llama"))
    assert "reasoning" not in groq and groq["temperature"] == 0.2
    o = m.adapt_payload(payload, m.Target("openai", "k", "gpt-5.4-mini"))
    assert o["max_completion_tokens"] == 10 and "max_tokens" not in o and "temperature" not in o
    assert m.adapt_payload(payload, m.Target("openai", "k", "gpt-4.1"))["max_tokens"] == 10


def test_middleware_reads_the_headers_per_request():
    app = FastAPI()

    @app.get("/c")
    def read():
        c = m.current_choice()
        return {"mode": c.mode, "provider": c.provider, "model": c.model, "key": bool(c.key)}

    app.add_middleware(m.ModelChoiceMiddleware)
    client = TestClient(app)
    got = client.get(
        "/c",
        headers={"X-Model-Mode": "custom", "X-LLM-Provider": "Groq", "X-LLM-Key": "gsk_1", "X-LLM-Model": "llama"},
    ).json()
    assert got == {"mode": "custom", "provider": "groq", "model": "llama", "key": True}
    assert client.get("/c").json() == {"mode": "auto", "provider": "", "model": "", "key": False}


def test_get_agent_uses_the_learners_key(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "openrouter_api_key", "")
    assert get_agent() is None
    token = m.set_choice(m.ModelChoice(mode="custom", provider="openai", key="sk-mine", model="gpt-4.1"))
    try:
        agent = get_agent()
        assert agent is not None and agent.model == "gpt-4.1"
    finally:
        m.reset_choice(token)


def _agent(handler, targets):
    return LessonAgent(targets=targets, http_transport=httpx.MockTransport(handler))


def test_falls_back_across_providers_and_sticks():
    tried = []

    def handler(request):
        tried.append((request.url.host, json.loads(request.content)["model"]))
        if request.url.host == "api.openai.com":
            return httpx.Response(429, json={"error": {"message": "quota"}})
        return httpx.Response(200, json=OK)

    agent = _agent(handler, [m.Target("openai", "sk", "gpt-4.1"), m.Target("openrouter", "or", "b")])
    asyncio.run(agent._post({"messages": []}))
    assert tried == [("api.openai.com", "gpt-4.1"), ("openrouter.ai", "b")]
    assert agent.model == "b"
    asyncio.run(agent._post({"messages": []}))
    assert tried[-1] == ("openrouter.ai", "b")


def test_each_target_gets_its_own_key():
    seen = []

    def handler(request):
        seen.append(request.headers["authorization"])
        return httpx.Response(401) if len(seen) == 1 else httpx.Response(200, json=OK)

    agent = _agent(handler, [m.Target("groq", "gsk_mine", "x"), m.Target("openrouter", "sk-or-srv", "y")])
    asyncio.run(agent._post({"messages": []}))
    assert seen == ["Bearer gsk_mine", "Bearer sk-or-srv"]


def test_an_error_body_counts_as_a_failure():
    def handler(request):
        if json.loads(request.content)["model"] == "a":
            return httpx.Response(200, json={"error": {"code": 502}})
        return httpx.Response(200, json=OK)

    agent = LessonAgent(api_key="k", model=["a", "b"], http_transport=httpx.MockTransport(handler))
    asyncio.run(agent._post({"messages": []}))
    assert agent.model == "b"


def test_every_model_failing_raises():
    agent = LessonAgent(
        api_key="k", model=["a", "b"], http_transport=httpx.MockTransport(lambda r: httpx.Response(503))
    )
    with pytest.raises(AgentUnavailableError, match="tried 2 models"):
        asyncio.run(agent._post({"messages": []}))


def test_list_models_filters_to_chat_models():
    def handler(request):
        assert request.headers["authorization"] == "Bearer sk-1"
        return httpx.Response(200, json={"data": [{"id": "gpt-4.1"}, {"id": "text-embedding-3-small"}, {"id": "tts-1"}]})

    got = asyncio.run(m.list_models("openai", "sk-1", transport=httpx.MockTransport(handler)))
    assert [x["id"] for x in got] == ["gpt-4.1"]


def test_list_models_google_keeps_the_key_out_of_the_url():
    def handler(request):
        assert "AIza" not in str(request.url)
        assert request.headers["x-goog-api-key"] == "AIza1"
        return httpx.Response(200, json={"models": [
            {"name": "models/gemini-3.8-flash", "displayName": "Gemini 3.8 Flash", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/text-embedding-004", "supportedGenerationMethods": ["embedContent"]},
        ]})

    got = asyncio.run(m.list_models("google", "AIza1", transport=httpx.MockTransport(handler)))
    assert got == [{"id": "gemini-3.8-flash", "name": "Gemini 3.8 Flash"}]


def test_list_models_rejected_key():
    with pytest.raises(PermissionError):
        asyncio.run(m.list_models("groq", "bad", transport=httpx.MockTransport(lambda r: httpx.Response(401))))


def test_models_endpoint_needs_a_provider_and_key():
    from app.main import app

    assert TestClient(app).get("/llm/models").status_code == 400
    assert "openai" in [p["id"] for p in TestClient(app).get("/llm/providers").json()["providers"]]
