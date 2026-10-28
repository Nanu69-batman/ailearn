"""Intent routing: the regex first, a small model only when the regex is unsure.

:func:`app.chat.intent.classify_intent` settles the clear cases ("hi",
"thanks", "who are you", "quiz me") instantly and for free. It cannot list
every way to ask about the assistant ("really?? what are your capabilities",
"can u make flashcards?"), and a miss sends the message through retrieval and
the general-knowledge fallback. For a message the regex leaves undecided, a
small fast model labels it instead.

Safety: a wrong "chat" label is the costly mistake (a subject question would
get an uncited, untagged answer), so the model only routes to chat at high
confidence. Any timeout, error or low-confidence label returns ``None``: the
ordinary grounded path, exactly as without the router. Plain subject questions
("what is a partial derivative") never reach the model, so they pay no wait.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import OrderedDict

from app.chat.intent import (
    OVERVIEW,
    SMALLTALK,
    STUDY_QUESTIONS,
    classify_intent,
    has_subject_content,
)

log = logging.getLogger(__name__)

#: Model label -> intent. ``concept`` means the ordinary grounded path.
LABELS: dict[str, str | None] = {
    "chat": SMALLTALK,
    "study_questions": STUDY_QUESTIONS,
    "overview": OVERVIEW,
    "concept": None,
}

#: Messages longer than this are real questions; the model is not consulted.
MAX_ROUTED_CHARS = 200

#: The message talks to or about the assistant.
_ADDRESSES_ASSISTANT_RE = re.compile(
    r"\b(?:you|your|yours|yourself|u|ur|malai|bot|assistant)\b", re.IGNORECASE
)

ROUTER_SYSTEM = (
    "You route messages a student sends to Malai, an AI study assistant that "
    "sits beside a lecture video. Label the message with exactly one of:\n"
    "- chat: a greeting, thanks, small talk, or a question about Malai itself "
    "(who it is, what it can do, its features, capabilities or limits, how to "
    "use it). It names no academic concept to explain.\n"
    "- concept: anything asking about an academic idea, term, formula, method, "
    "example, or the lesson's content, even if phrased casually or addressed "
    "to Malai (\"can you explain the chain rule\" is concept).\n"
    "- study_questions: the student wants practice, exam or quiz questions.\n"
    "- overview: the student wants a recap or summary of what has been covered.\n"
    "If unsure between chat and concept, choose concept. Return JSON with the "
    "label and your confidence from 0 to 1."
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "label": {"type": "string", "enum": list(LABELS)},
        "confidence": {"type": "number"},
    },
    "required": ["label", "confidence"],
}


def worth_asking(message: str) -> bool:
    """True for a regex-undecided message the model might label as chat."""
    msg = (message or "").strip()
    if not msg or len(msg) > MAX_ROUTED_CHARS:
        return False
    return bool(_ADDRESSES_ASSISTANT_RE.search(msg)) or not has_subject_content(msg)


class ModelIntentRouter:
    """Regex first; for an undecided message, ask a small Gemini model."""

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout_s: float = 2.5,
        threshold: float = 0.85,
        cache_size: int = 512,
    ) -> None:
        if not api_key:
            raise ValueError("LLM_API_KEY not set; ModelIntentRouter unavailable")
        self._api_key = api_key
        self._model = model
        self._timeout_s = timeout_s
        self._threshold = threshold
        self._cache: OrderedDict[str, str | None] = OrderedDict()
        self._cache_size = cache_size
        self._client = None

    async def route(self, message: str) -> str | None:
        intent = classify_intent(message)
        if intent is not None or not worth_asking(message):
            return intent
        key = " ".join(message.lower().split())
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        try:
            label, confidence = await asyncio.wait_for(
                asyncio.to_thread(self._ask, message), timeout=self._timeout_s
            )
        except Exception as exc:  # noqa: BLE001 -- any failure means the regex answer
            log.warning("intent router unavailable (%r); using the grounded path", exc)
            return None
        routed = LABELS.get(label) if confidence >= self._threshold else None
        log.info("intent router: %s (%.2f) -> %s", label, confidence, routed)
        self._cache[key] = routed
        if len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
        return routed

    def _ask(self, message: str) -> tuple[str, float]:
        from google import genai
        from google.genai import types

        # One client for the process: building one per call cost ~1 s.
        # Never log or echo the key; the client holds it in memory only.
        if self._client is None:
            self._client = genai.Client(api_key=self._api_key)
        resp = self._client.models.generate_content(
            model=self._model,
            contents=message,
            config=types.GenerateContentConfig(
                system_instruction=ROUTER_SYSTEM,
                temperature=0,
                response_mime_type="application/json",
                response_schema=_SCHEMA,
            ),
        )
        data = json.loads(resp.text or "{}")
        return str(data.get("label", "concept")), float(data.get("confidence", 0))


_shared: ModelIntentRouter | None = None


def get_intent_router() -> ModelIntentRouter | None:
    """The model router when enabled and a Gemini key is set, else ``None``.

    One router per process: the chat service is built per request, and a
    fresh router would lose its cache and pay the client set-up every time.
    """
    from app.config import settings

    global _shared
    if not (settings.intent_router_enabled and settings.llm_api_key):
        return None
    if _shared is None:
        _shared = ModelIntentRouter(
            api_key=settings.llm_api_key,
            model=settings.intent_router_model,
            timeout_s=settings.intent_router_timeout_s,
        )
    return _shared
