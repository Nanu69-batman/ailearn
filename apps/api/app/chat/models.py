"""Which models answer a request: the learner's mode, or their own key and model.

The learner picks under the chat input:

* a mode, ``auto``, ``fast``, ``balanced`` or ``powerful``: free OpenRouter
  models on the server's key. ``auto`` is OpenRouter's own free router
  (``openrouter/free``); the others are ordered lists of free models with
  tool calling, tried in turn so one busy model never sinks an answer.
* or, under Advanced, ``custom``: their own key for any supported provider and
  one model from that key's list (``GET /llm/models``). The server's free
  models stay behind it as the fallback.

Every provider here speaks the OpenAI chat-completions format (tool calling
included), so one client covers them all; :func:`adapt_payload` smooths the
few per-provider differences.

The choice travels as request headers and is read once per request by
:class:`ModelChoiceMiddleware` into a context variable, so every model call in
that request sees it. The key is never logged and never stored server-side.
"""

from __future__ import annotations

import logging
import re
from contextvars import ContextVar
from dataclasses import dataclass

from fastapi import APIRouter, HTTPException

log = logging.getLogger(__name__)

MODES = ("auto", "fast", "balanced", "powerful", "custom")
DEFAULT_MODE = "auto"

#: OpenRouter's router over free models.
FREE_ROUTER = "openrouter/free"

#: Free models with tool calling, per mode. ``openrouter/free`` closes every
#: list: if each named model is busy, the router still finds one that is not.
FREE_MODELS: dict[str, list[str]] = {
    "auto": [FREE_ROUTER],
    "fast": [
        "nvidia/nemotron-3.5-lightning:free",
        "thinkingmachines/inkling-small:free",
        "google/gemma-4-26b-a4b-it:free",
        FREE_ROUTER,
    ],
    "balanced": [
        "nvidia/nemotron-3.5-super-vl:free",
        "google/gemma-4-31b-it:free",
        "thinkingmachines/inkling:free",
        FREE_ROUTER,
    ],
    "powerful": [
        "nvidia/nemotron-3-ultra-550b-a55b:free",
        "thinkingmachines/inkling:free",
        "nvidia/nemotron-3.5-super-vl:free",
        FREE_ROUTER,
    ],
}


@dataclass(frozen=True)
class Provider:
    label: str
    #: OpenAI-compatible base; chat is ``{base}/chat/completions``.
    base_url: str


PROVIDERS: dict[str, Provider] = {
    "openrouter": Provider("OpenRouter", "https://openrouter.ai/api/v1"),
    "openai": Provider("OpenAI", "https://api.openai.com/v1"),
    "anthropic": Provider("Anthropic", "https://api.anthropic.com/v1"),
    "google": Provider("Google Gemini", "https://generativelanguage.googleapis.com/v1beta/openai"),
    "groq": Provider("Groq", "https://api.groq.com/openai/v1"),
    "mistral": Provider("Mistral", "https://api.mistral.ai/v1"),
    "deepseek": Provider("DeepSeek", "https://api.deepseek.com"),
    "xai": Provider("xAI", "https://api.x.ai/v1"),
}

#: Longest key / model id accepted from a header.
MAX_KEY_LEN = 300
MAX_MODEL_LEN = 200

MODE_HEADER = "x-model-mode"
PROVIDER_HEADER = "x-llm-provider"
KEY_HEADER = "x-llm-key"
MODEL_HEADER = "x-llm-model"


@dataclass(frozen=True)
class ModelChoice:
    mode: str = DEFAULT_MODE
    #: The learner's own provider, key and model (``custom`` mode), or "".
    provider: str = ""
    key: str = ""
    model: str = ""

    @property
    def custom(self) -> bool:
        return self.mode == "custom" and bool(self.provider and self.key and self.model)


@dataclass(frozen=True)
class Target:
    """One model to try: where it lives and the key to reach it with."""

    provider: str
    api_key: str
    model: str

    @property
    def url(self) -> str:
        return PROVIDERS[self.provider].base_url + "/chat/completions"


_choice: ContextVar[ModelChoice] = ContextVar("model_choice", default=ModelChoice())


def normalize_mode(mode: str | None) -> str:
    mode = (mode or "").strip().lower()
    return mode if mode in MODES else DEFAULT_MODE


def _clean(value: str | None, limit: int) -> str:
    value = (value or "").strip()
    if not value or len(value) > limit or not value.isascii() or not value.isprintable():
        return ""
    return value


def normalize_key(key: str | None) -> str:
    return _clean(key, MAX_KEY_LEN)


def normalize_provider(provider: str | None) -> str:
    provider = (provider or "").strip().lower()
    return provider if provider in PROVIDERS else ""


def current_choice() -> ModelChoice:
    return _choice.get()


def set_choice(choice: ModelChoice):
    """Set the choice for this context; returns a token for :func:`reset_choice`."""
    return _choice.set(choice)


def reset_choice(token) -> None:
    _choice.reset(token)


def models_for(mode: str) -> list[str]:
    """The free OpenRouter fallback list for ``mode`` (custom falls back to auto)."""
    mode = normalize_mode(mode)
    return list(FREE_MODELS.get(mode, FREE_MODELS[DEFAULT_MODE]))


def targets_for(choice: ModelChoice, server_key: str) -> list[Target]:
    """What to try, in order, for this request."""
    targets: list[Target] = []
    if choice.custom:
        targets.append(Target(choice.provider, choice.key, choice.model))
    if server_key:
        targets.extend(Target("openrouter", server_key, m) for m in models_for(choice.mode))
    return targets


#: Reasoning models on OpenAI take ``max_completion_tokens`` and only the
#: default temperature.
_OPENAI_REASONING = re.compile(r"^(o\d|gpt-5)")


def adapt_payload(payload: dict, target: Target) -> dict:
    """The request body for ``target``: same OpenAI shape, provider quirks smoothed."""
    body = {**payload, "model": target.model}
    if target.provider != "openrouter":
        body.pop("reasoning", None)  # OpenRouter's own parameter
    if target.provider == "openai" and _OPENAI_REASONING.match(target.model):
        if "max_tokens" in body:
            body["max_completion_tokens"] = body.pop("max_tokens")
        body.pop("temperature", None)
    return body


def request_headers(target: Target) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {target.api_key}"}
    if target.provider == "openrouter":
        headers["HTTP-Referer"] = "https://ailearn-theta.vercel.app"
        headers["X-Title"] = "Malai Pioneer"
    return headers


class ModelChoiceMiddleware:
    """Read the model headers into the request's context.

    Pure ASGI rather than ``BaseHTTPMiddleware`` so the context variable is
    visible to the endpoint and to a streaming response's body.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        token = set_choice(
            ModelChoice(
                mode=normalize_mode(headers.get(MODE_HEADER)),
                provider=normalize_provider(headers.get(PROVIDER_HEADER)),
                key=normalize_key(headers.get(KEY_HEADER)),
                model=_clean(headers.get(MODEL_HEADER), MAX_MODEL_LEN),
            )
        )
        try:
            await self.app(scope, receive, send)
        finally:
            reset_choice(token)


# --- GET /llm/models: the models a learner's own key can use ----------------

router = APIRouter()

#: Ids on OpenAI-style lists that are not chat models.
_NOT_CHAT = re.compile(
    r"embed|tts|whisper|dall-e|image|audio|moderation|realtime|transcribe|"
    r"search|computer-use|guard|rerank|ocr|babbage|davinci|veo|imagen|lyria",
    re.IGNORECASE,
)


async def list_models(provider: str, key: str, *, transport=None) -> list[dict]:
    """``[{id, name}]`` of the chat models ``key`` can use at ``provider``.

    Raises ``PermissionError`` when the provider rejects the key, and
    ``RuntimeError`` when it cannot be reached.
    """
    import httpx

    base = PROVIDERS[provider].base_url
    async with httpx.AsyncClient(timeout=20, transport=transport) as client:
        try:
            if provider == "anthropic":
                resp = await client.get(
                    f"{base}/models",
                    params={"limit": 1000},
                    headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                )
            elif provider == "google":
                # The native list says which models generate text; the key goes
                # in a header, never the URL (httpx logs URLs).
                resp = await client.get(
                    "https://generativelanguage.googleapis.com/v1beta/models",
                    params={"pageSize": 1000},
                    headers={"x-goog-api-key": key},
                )
            elif provider == "openrouter":
                # The model list is public; /key is what checks the key.
                check = await client.get(f"{base}/key", headers={"Authorization": f"Bearer {key}"})
                if check.status_code in (401, 403):
                    raise PermissionError("key rejected")
                resp = await client.get(f"{base}/models")
            else:
                resp = await client.get(f"{base}/models", headers={"Authorization": f"Bearer {key}"})
        except httpx.HTTPError as exc:
            raise RuntimeError(f"{provider} unreachable: {type(exc).__name__}") from exc
    if resp.status_code in (400, 401, 403):
        raise PermissionError("key rejected")
    if resp.status_code != 200:
        raise RuntimeError(f"{provider} http {resp.status_code}")
    try:
        data = resp.json()
    except ValueError as exc:
        raise RuntimeError(f"{provider} returned non-JSON") from exc

    out: list[dict] = []
    if provider == "google":
        for m in data.get("models") or []:
            if "generateContent" not in (m.get("supportedGenerationMethods") or []):
                continue
            mid = str(m.get("name", "")).removeprefix("models/")
            if mid and not _NOT_CHAT.search(mid):
                out.append({"id": mid, "name": m.get("displayName") or mid})
    else:
        for m in data.get("data") or []:
            mid = str(m.get("id", ""))
            if not mid or _NOT_CHAT.search(mid):
                continue
            # Only OpenRouter says which models call tools; the agent needs them.
            if provider == "openrouter" and "tools" not in (m.get("supported_parameters") or []):
                continue
            out.append({"id": mid, "name": m.get("display_name") or m.get("name") or mid})
    out.sort(key=lambda m: m["name"].lower())
    return out


@router.get("/llm/models")
async def get_llm_models() -> dict:
    """The models the key in ``X-LLM-Key`` can use at ``X-LLM-Provider``."""
    choice = current_choice()
    if not choice.provider or not choice.key:
        raise HTTPException(status_code=400, detail="choose a provider and paste a key")
    try:
        models = await list_models(choice.provider, choice.key)
    except PermissionError as exc:
        raise HTTPException(status_code=400, detail="the provider rejected this key") from exc
    except RuntimeError as exc:
        log.warning("model list failed: %s", exc)
        raise HTTPException(status_code=502, detail="could not reach the provider") from exc
    return {"provider": choice.provider, "models": models}


@router.get("/llm/providers")
def get_llm_providers() -> dict:
    return {"providers": [{"id": k, "label": p.label} for k, p in PROVIDERS.items()]}
