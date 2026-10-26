"""LLM provider interface. Deterministic stub by default; OpenAI only with key."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from app.chat.prompts import REFUSAL_TEXT, extract_cited_ids

log = logging.getLogger(__name__)

#: Stable first line of ``prompts.SUPPLEMENTAL_SYSTEM``. Providers use it to
#: recognise a supplemental turn without importing the whole prompt: the
#: supplemental prompt is the ONLY place this string appears, so it is an
#: unambiguous marker rather than a heuristic.
SUPPLEMENTAL_SYSTEM_MARKER = (
    "You are a teaching assistant helping a learner with a course they are part"
)


@dataclass
class LLMResult:
    text: str
    cited_ids: list[str] = field(default_factory=list)


async def _traced(label: str, impl, *, system: str, user_prompt: str, passages: Sequence, mode: str):
    """Run one provider call inside a developer-view trace span."""
    from app import devtrace
    from app.chat import streaming
    from app.chat.prompts import with_style

    system = with_style(system)
    sink = streaming.current()
    if sink is not None:
        sink.reset()
        sink.status("Writing the answer")
    with devtrace.span(
        "llm", title="Generate the answer", at=label,
        data={
            "mode": mode,
            "passages_in_prompt": len(passages),
            "passage_ids": [getattr(p, "chunk_id", None) for p in list(passages)[:10]],
            "prompt_chars": len(system) + len(user_prompt),
            "prompt_tail": devtrace.preview(user_prompt[-400:], 240),
        },
    ) as out:
        result = await impl(system=system, user_prompt=user_prompt, passages=passages, mode=mode)
        if sink is not None and sink.deltas_since_reset == 0:
            # A provider that does not stream (the stub, a cache hit) still
            # shows its reply, all at once.
            sink.delta(result.text)
        out["reply"] = devtrace.preview(result.text, 260)
        out["cited_ids"] = list(result.cited_ids)[:10]
        return result


class LLMProvider(Protocol):
    async def complete(
        self,
        *,
        system: str,
        user_prompt: str,
        passages: Sequence,
        mode: str,
    ) -> LLMResult: ...


class DeterministicStubLLM:
    """Test/default provider: echoes refusal or evidence-bound answers.

    Never invents citations: cited IDs always come from ``passages``.
    """

    def __init__(self) -> None:
        self.last_system: str = ""
        self.last_user_prompt: str = ""
        self.last_mode: str = ""
        self.last_passage_ids: list[str] = []
        self.calls: int = 0

    async def complete(
        self, *, system: str, user_prompt: str, passages: Sequence, mode: str
    ) -> LLMResult:
        return await _traced(
            "API · offline stub LLM", self._complete,
            system=system, user_prompt=user_prompt, passages=passages, mode=mode,
        )

    async def _complete(
        self, *, system: str, user_prompt: str, passages: Sequence, mode: str
    ) -> LLMResult:
        self.last_system = system
        self.last_user_prompt = user_prompt
        self.last_mode = mode
        self.last_passage_ids = [p.chunk_id for p in passages]
        self.calls += 1
        # The supplemental prompt is identified by its system prompt rather than
        # by the provider knowing about modes. This stub has no general
        # knowledge to offer and echoing a passage would manufacture exactly the
        # un-cited-from-the-video answer the mode forbids, so it declines and the
        # service falls back to the ordinary grounded refusal.
        if SUPPLEMENTAL_SYSTEM_MARKER in system:
            return LLMResult(text=REFUSAL_TEXT, cited_ids=[])
        if not passages:
            return LLMResult(text=REFUSAL_TEXT, cited_ids=[])
        if mode == "quiz_json":
            # A structured practice quiz (app.chat.quiz): one subjective
            # question per passage, quoting it as the model answer.
            questions = [
                {
                    "type": "subjective",
                    "prompt": "Explain the key point of this part of the lesson.",
                    "model_answer": p.text[:300],
                    "key_points": [p.text[:80]],
                    "explanation": "This is what the passage states.",
                    "chunk_id": p.chunk_id,
                }
                for p in passages[:5]
            ]
            return LLMResult(text=json.dumps({"questions": questions}))
        if mode == "quiz":
            first = passages[0]
            lines = ["Quiz based on covered material:"]
            for i, p in enumerate(passages[:2], start=1):
                lines.append(
                    f"Q{i}: What is the key point of this passage? "
                    f"(see [chunk_id={p.chunk_id}])"
                )
                snippet = p.text[:120].replace("\n", " ")
                lines.append(f"A{i}: {snippet} [chunk_id={p.chunk_id}]")
            return LLMResult(
                text="\n".join(lines),
                cited_ids=[p.chunk_id for p in passages[:2]],
            )
        if mode == "summarize":
            joined = " ".join(p.text for p in passages[:3])
            cites = " ".join(f"[chunk_id={p.chunk_id}]" for p in passages[:3])
            return LLMResult(
                text=f"Summary of what we've covered so far: {joined} {cites}".strip(),
                cited_ids=[p.chunk_id for p in passages[:3]],
            )
        if mode == "analogy":
            first = passages[0]
            return LLMResult(
                text=(
                    f"Think of it like a familiar process: {first.text} "
                    f"[chunk_id={first.chunk_id}]"
                ),
                cited_ids=[first.chunk_id],
            )
        if mode == "explain_simple":
            first = passages[0]
            return LLMResult(
                text=f"In simple terms: {first.text} [chunk_id={first.chunk_id}]",
                cited_ids=[first.chunk_id],
            )
        if mode == "compare":
            cites = " ".join(f"[chunk_id={p.chunk_id}]" for p in passages[:2])
            joined = " | ".join(p.text for p in passages[:2])
            return LLMResult(
                text=f"Comparison from the material: {joined} {cites}".strip(),
                cited_ids=[p.chunk_id for p in passages[:2]],
            )
        first = passages[0]
        return LLMResult(
            text=f"{first.text} [chunk_id={first.chunk_id}]",
            cited_ids=[first.chunk_id],
        )


class OpenAIChatLLM:
    """OpenAI-compatible chat provider. Constructed only when LLM_API_KEY is set."""

    def __init__(self, api_key: str, model: str = "gpt-4o-mini") -> None:
        if not api_key:
            raise ValueError("LLM_API_KEY not set; OpenAIChatLLM unavailable")
        self._api_key = api_key
        self._model = model

    async def complete(
        self, *, system: str, user_prompt: str, passages: Sequence, mode: str
    ) -> LLMResult:
        import httpx

        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
        }
        headers = {"Authorization": f"Bearer {self._api_key}"}
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
        text = data["choices"][0]["message"]["content"] or ""
        return LLMResult(text=text, cited_ids=extract_cited_ids(text))


class GeminiChatLLM:
    """Gemini chat provider via google-genai. Constructed only when set."""

    def __init__(self, api_key: str, model: str = "gemini-3.5-flash-lite") -> None:
        if not api_key:
            raise ValueError("LLM_API_KEY not set; GeminiChatLLM unavailable")
        self._api_key = api_key
        self._model = model

    async def complete(
        self, *, system: str, user_prompt: str, passages: Sequence, mode: str
    ) -> LLMResult:
        return await _traced(
            f"Gemini · {self._model}", self._complete,
            system=system, user_prompt=user_prompt, passages=passages, mode=mode,
        )

    async def _complete(
        self, *, system: str, user_prompt: str, passages: Sequence, mode: str
    ) -> LLMResult:
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise RuntimeError("google-genai package not installed") from exc
        from app.cache import cache_enabled, get_cache, sha1hex  # lazy import

        cache_key = "chat:" + self._model + ":0.2:" + sha1hex(system, user_prompt)
        if cache_enabled():
            cached = get_cache("chat").get(cache_key)
            if cached is not None:
                log.info("chat cache hit (mode %s)", mode)
                return LLMResult(
                    text=str(cached["text"]),
                    cited_ids=list(cached.get("cited_ids") or []),
                )
        # Never log or echo the key; the client holds it in memory only.
        client = genai.Client(api_key=self._api_key)
        config = types.GenerateContentConfig(system_instruction=system, temperature=0.2)
        from app.chat import streaming

        sink = streaming.current()
        # The SDK calls are blocking. On a worker thread, so a 20-40 s answer
        # does not freeze every other request on the server meanwhile.
        if sink is None:
            resp = await asyncio.to_thread(
                client.models.generate_content,
                model=self._model,
                contents=user_prompt,
                config=config,
            )
            text = (resp.text or "").strip()
        else:
            def pull() -> str:
                parts: list[str] = []
                for chunk in client.models.generate_content_stream(
                    model=self._model, contents=user_prompt, config=config
                ):
                    piece = chunk.text or ""
                    parts.append(piece)
                    sink.delta(piece)
                return "".join(parts)

            text = (await asyncio.to_thread(pull)).strip()
        result = LLMResult(text=text, cited_ids=extract_cited_ids(text))
        if cache_enabled():
            get_cache("chat").set(
                cache_key, {"text": result.text, "cited_ids": result.cited_ids}
            )
        return result


def get_llm_provider():
    """Return the Gemini provider only when a key is configured, else stub."""
    from app.config import settings

    if settings.llm_api_key:
        try:
            return GeminiChatLLM(api_key=settings.llm_api_key, model=settings.llm_model)
        except ValueError:
            log.warning("LLM_API_KEY invalid; falling back to DeterministicStubLLM")
    return DeterministicStubLLM()
