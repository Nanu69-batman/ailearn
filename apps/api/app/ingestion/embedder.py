"""Embeddings: offline by default, Gemini when explicitly configured.

Two providers, one interface:

* :class:`HashingEmbedder` -- deterministic, offline, no model download. This is
  the default so tests and CI never touch the network.
* :class:`GeminiEmbedder` -- the configured ``EMBEDDING_MODEL``.

Both expose ``embed_documents``/``embed_query`` so the Chroma store can wrap
either one in an ``EmbeddingFunction`` without knowing which it got.

Vector width is a correctness property, not a tuning knob: chunks written by
one width and queries computed at another produce meaningless distances. The
mapping is pinned in :data:`EMBED_DIM` / :data:`GEMINI_EMBED_DIM` and surfaced
through ``Settings.expected_embedding_dim()``, which is the single place both
sides must agree (see ``app/config.py``).
"""

from __future__ import annotations

import hashlib
import logging
import math
import re

from app.ingestion import backoff as backoff_mod

log = logging.getLogger(__name__)

#: Offline hashing embedder width.
EMBED_DIM = 1536
#: Gemini text-embedding width.
GEMINI_EMBED_DIM = 3072

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall((text or "").lower())


class HashingEmbedder:
    """Deterministic hashed bag-of-tokens embedder. No network, no model file.

    Uses the signed hashing trick: each token contributes to a fixed set of
    dimensions via ``sha256``, so the same token always lands in the same
    buckets. Output is L2-normalized so Chroma's cosine distance behaves like
    cosine similarity. This is a *vectorizer*, not a ranker -- all ranking is
    Chroma's job.
    """

    def __init__(self, dim: int = EMBED_DIM) -> None:
        if dim <= 0:
            raise ValueError("dim must be positive")
        self.dim = dim

    def _vector(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        tokens = tokenize(text)
        if not tokens:
            return vec
        for tok in tokens:
            digest = hashlib.sha256(tok.encode("utf-8")).digest()
            for offset in (0, 8, 16):
                idx = int.from_bytes(digest[offset : offset + 4], "big") % self.dim
                sign = 1.0 if digest[offset + 4] & 1 else -1.0
                vec[idx] += sign
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)

    def __call__(self, text: str) -> list[float]:
        return self.embed_query(text)


class GeminiEmbedder:
    """``google-genai`` text embeddings, batched.

    Imported lazily so a missing key or SDK never breaks module import.
    """

    def __init__(self, model: str, api_key: str) -> None:
        from google import genai

        self._client = genai.Client(api_key=api_key)
        self._model = model
        self.dim = GEMINI_EMBED_DIM

    #: Texts per ``embed_content`` call.
    BATCH_SIZE = 100

    #: Attempts and backoff tuned to *this* quota, which is the stingier of the
    #: two and measures requests per minute, not batches.
    #:
    #: The batch form returns a single pooled vector rather than one per text,
    #: so the real path is one request per chunk -- ~265 requests for a 655s
    #: lecture against a 100/min free-tier cap. The quota error asks for ~23s,
    #: so a 2s base would burn all five attempts inside a single quota window
    #: and fail a pass that had already cost six minutes. 15s x 8 attempts
    #: spans ~4 minutes of quota refill, which is enough for the whole write.
    EMBED_MAX_ATTEMPTS = 8
    EMBED_BACKOFF_S = 15.0
    EMBED_BACKOFF_CAP_S = 60.0

    def _embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts, verifying one vector comes back per input.

        The batch form of ``embed_content`` does NOT reliably return one
        embedding per content: passing a 5-element list returned a single
        vector that matched none of the five individually-embedded texts (i.e.
        a pooled/averaged vector, not a per-text one). Indexing with that would
        give every chunk in a batch the same embedding and quietly destroy
        retrieval quality with no error raised.

        So the count is checked against the input count, and a mismatch falls
        back to per-text calls. Slower, but every returned vector is provably
        that text's own embedding.
        """
        out: list[list[float]] = []
        for start in range(0, len(texts), self.BATCH_SIZE):
            batch = texts[start : start + self.BATCH_SIZE]
            vectors = self._embed_batch(batch)
            if len(vectors) != len(batch):
                log.warning(
                    "gemini embed_content returned %d vector(s) for %d text(s); "
                    "falling back to per-text embedding",
                    len(vectors),
                    len(batch),
                )
                vectors = [self._embed_batch([t])[0] for t in batch]
            out.extend(vectors)
        return out

    def _embed_once(self, batch: list[str]) -> list[list[float]]:
        resp = self._client.models.embed_content(model=self._model, contents=batch)
        return [list(item.values) for item in (resp.embeddings or [])]

    def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        """Embed one batch, surviving the free tier's per-minute embedding cap.

        A 655s lecture indexes ~250 chunks, which is ~3 batches of 100 against
        a 100 requests/minute free-tier quota -- and the chunk write is the
        *last* step of an ingest that has already spent minutes transcribing
        and describing. An unretried 429 there throws away the entire pass and
        fails the job, so the wait-and-retry is bounded and shared with the
        describe loop rather than bespoke to this call.
        """
        return backoff_mod.call_with_retry(
            lambda: self._embed_once(batch),
            what=f"embed_content({self._model}, {len(batch)} text(s))",
            max_attempts=self.EMBED_MAX_ATTEMPTS,
            backoff_s=self.EMBED_BACKOFF_S,
            backoff_cap_s=self.EMBED_BACKOFF_CAP_S,
        )

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text])[0]


def get_default_embedder():
    """Ingestion-side embedder. Gemini only when a key is configured."""
    from app.config import settings

    if settings.embedding_api_key:
        return GeminiEmbedder(settings.embedding_model, settings.embedding_api_key)
    return HashingEmbedder()


def get_embedder():
    """Retrieval-side embedder. Same choice as ingestion -- see config docstring."""
    return get_default_embedder()