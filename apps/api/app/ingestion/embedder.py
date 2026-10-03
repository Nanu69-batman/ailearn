"""Embedding providers.

The same embed_model must be used at ingest and query time; every chunk
records the model name so retrieval can filter/match.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
from typing import Protocol, runtime_checkable

from app.config import settings

log = logging.getLogger(__name__)

EMBED_DIM = 1536

GEMINI_EMBED_DIM = 3072
GEMINI_EMBED_MODEL = "gemini-embedding-2"
GEMINI_BATCH_SIZE = 100


@runtime_checkable
class EmbeddingProvider(Protocol):
    name: str

    def embed(self, text: str) -> list[float]: ...

    def embed_batch(self, texts: list[str]) -> list[list[float]]: ...


class HashingEmbedder:
    """Deterministic, dependency-free 1536-dim embedder (feature hashing)."""

    name = "hashing-v1"

    def __init__(self, dim: int = EMBED_DIM) -> None:
        self.dim = dim

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for tok in re.findall(r"\w+", text.lower()):
            h = int(hashlib.sha256(tok.encode("utf-8")).hexdigest(), 16)
            bucket = h % self.dim
            sign = 1.0 if (h >> 64) & 1 else -1.0
            vec[bucket] += sign
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]


class OpenAIEmbedder:
    """Stub: real call only when an API key is configured."""

    name = "openai-text-embedding-3-small"

    def __init__(
        self, api_key: str = "", model: str = "text-embedding-3-small"
    ) -> None:
        self.api_key = api_key or settings.embedding_api_key
        self.model = model
        self.name = f"openai-{model}"

    def embed(self, text: str) -> list[float]:
        if not self.api_key:
            raise RuntimeError("EMBEDDING_API_KEY not set; OpenAIEmbedder unavailable")
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not self.api_key:
            raise RuntimeError("EMBEDDING_API_KEY not set; OpenAIEmbedder unavailable")
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("openai package not installed") from exc
        client = OpenAI(api_key=self.api_key)
        resp = client.embeddings.create(model=self.model, input=texts)
        return [d.embedding for d in resp.data]


class GeminiEmbedder:
    """Live Gemini embedding provider via google-genai (native 3072-dim).

    Raises a clear error when no key is configured; imports google-genai
    lazily so offline/test environments never require it.
    """

    def __init__(self, api_key: str = "", model: str = "") -> None:
        self.api_key = api_key or settings.embedding_api_key
        self.model = model or settings.embedding_model
        self.name = f"gemini-{self.model}"
        self.dim = GEMINI_EMBED_DIM

    def _client(self):  # lazy: google-genai imported only when needed
        if not self.api_key:
            raise RuntimeError("EMBEDDING_API_KEY not set; GeminiEmbedder unavailable")
        try:
            from google import genai
        except ImportError as exc:
            raise RuntimeError("google-genai package not installed") from exc
        return genai.Client(api_key=self.api_key)

    def embed(self, text: str) -> list[float]:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not self.api_key:
            raise RuntimeError("EMBEDDING_API_KEY not set; GeminiEmbedder unavailable")
        if not texts:
            return []
        from google.genai import types  # lazy: keeps offline/test imports clean

        from app.cache import cache_enabled, get_cache, sha1hex  # lazy: no import cost

        cache = get_cache("embeddings") if cache_enabled() else None
        out: list[list[float] | None] = [None] * len(texts)
        missing: list[int] = []
        if cache is not None:
            for i, text in enumerate(texts):
                vec = cache.get("emb:" + self.model + ":" + sha1hex(text))
                if vec is not None:
                    out[i] = [float(v) for v in vec]
                else:
                    missing.append(i)
        else:
            missing = list(range(len(texts)))
        if missing:
            client = self._client()
            for i in range(0, len(missing), GEMINI_BATCH_SIZE):
                idx = missing[i : i + GEMINI_BATCH_SIZE]
                batch = [texts[j] for j in idx]
                # NOTE: contents must be Content objects — a bare list of
                # strings is coerced into ONE multi-part content (1 embedding).
                req = [
                    types.Content(
                        role="user", parts=[types.Part.from_text(text=t)]
                    )
                    for t in batch
                ]
                resp = client.models.embed_content(model=self.model, contents=req)
                got = [list(e.values) for e in resp.embeddings]
                if len(got) != len(batch):
                    raise RuntimeError(
                        f"embedding count mismatch: sent {len(batch)}, got {len(got)}"
                    )
                for j, vec in zip(idx, got):
                    out[j] = vec
                    if cache is not None:
                        cache.set("emb:" + self.model + ":" + sha1hex(texts[j]), vec)
            log.info(
                "embed cache: %d/%d hits (model %s)",
                len(texts) - len(missing),
                len(texts),
                self.model,
            )
        assert all(v is not None for v in out)
        return [v for v in out if v is not None]


def get_embedder() -> EmbeddingProvider:
    """Return Gemini when EMBEDDING_API_KEY is set, else the offline hasher."""
    if settings.embedding_api_key:
        return GeminiEmbedder()
    return HashingEmbedder()


def get_default_embedder() -> EmbeddingProvider:
    return get_embedder()
