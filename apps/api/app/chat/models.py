"""Which OpenRouter models answer a request: the learner's mode, and their key.

The learner picks a mode under the chat input (``auto``, ``fast``,
``balanced``, ``powerful``) and may paste their own OpenRouter key (BYOK).
Both travel as request headers and are read once per request by
:class:`ModelChoiceMiddleware` into a context variable, so every model call in
that request (agent turn, quiz, title) sees them without threading them
through each signature.

Each mode is an ordered list of models. :class:`app.chat.agent.LessonAgent`
tries them in order and moves to the next on a quota, outage or unavailable
model, so one busy free model never sinks an answer.

* ``auto`` is OpenRouter's own free router (``openrouter/free``): it picks a
  free model that supports the request's features (tool calling).
* Without a key of their own the learner gets the free lists (the server's key
  has no credits). With one, the paid list for the mode comes first and the
  free list stays behind it as the fallback.

The key is never logged and never stored server-side; it lives in the
learner's browser and is sent per request.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass

MODES = ("auto", "fast", "balanced", "powerful")
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

#: Paid models with tool calling, tried first when the learner brings a key.
BYOK_MODELS: dict[str, list[str]] = {
    "auto": [],
    "fast": [
        "deepseek/deepseek-v4.1-flash",
        "anthropic/claude-haiku-5.5",
        "z-ai/glm-5.3-flash",
    ],
    "balanced": [
        "google/gemini-3.8-flash",
        "openai/gpt-5.6-luna",
        "deepseek/deepseek-v4-pro",
    ],
    "powerful": [
        "anthropic/claude-sonnet-5.5",
        "openai/gpt-5.6-terra",
        "google/gemini-3.1-pro-preview",
    ],
}

#: Longest key accepted from the header; OpenRouter keys are ~73 characters.
MAX_KEY_LEN = 200

MODE_HEADER = "x-model-mode"
KEY_HEADER = "x-openrouter-key"


@dataclass(frozen=True)
class ModelChoice:
    mode: str = DEFAULT_MODE
    #: The learner's own OpenRouter key, or "" to use the server's.
    byok_key: str = ""


_choice: ContextVar[ModelChoice] = ContextVar("model_choice", default=ModelChoice())


def normalize_mode(mode: str | None) -> str:
    mode = (mode or "").strip().lower()
    return mode if mode in MODES else DEFAULT_MODE


def normalize_key(key: str | None) -> str:
    key = (key or "").strip()
    if not key or len(key) > MAX_KEY_LEN or not key.isascii() or not key.isprintable():
        return ""
    return key


def current_choice() -> ModelChoice:
    return _choice.get()


def set_choice(choice: ModelChoice):
    """Set the choice for this context; returns a token for :func:`reset_choice`."""
    return _choice.set(choice)


def reset_choice(token) -> None:
    _choice.reset(token)


def models_for(mode: str, *, byok: bool) -> list[str]:
    """The ordered fallback list for ``mode``."""
    mode = normalize_mode(mode)
    models = (BYOK_MODELS[mode] if byok else []) + FREE_MODELS[mode]
    return list(dict.fromkeys(models))


class ModelChoiceMiddleware:
    """Read the mode and BYOK key headers into the request's context.

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
                byok_key=normalize_key(headers.get(KEY_HEADER)),
            )
        )
        try:
            await self.app(scope, receive, send)
        finally:
            reset_choice(token)
