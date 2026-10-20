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
from collections.abc import Callable

from app.ingestion import backoff as backoff_mod

log = logging.getLogger(__name__)

#: Offline hashing embedder width.
EMBED_DIM = 1536
#: Gemini text-embedding width.
GEMINI_EMBED_DIM = 3072

_TOKEN_RE = re.compile(r"[a-z0-9]+")


class EmbeddingAlignmentError(RuntimeError):
    """A vector set came back that cannot be trusted to line up with its texts.

    Raised rather than indexed. Every downstream consumer zips vectors against
    chunks *positionally*, so a wrong-length or mis-ordered response would
    silently attach the wrong vector to the wrong chunk -- retrieval degrades
    quietly and nothing ever reports an error. Failing the ingest is loud and
    recoverable; corrupt embeddings are neither.
    """


#: Sink for embedding progress, installed by the ingest job for the duration of
#: a write. Module-level and process-global on purpose: the store owns the
#: embedder instance and is not ours to rewire, but the embedder is where the
#: progress actually exists. ``None`` means nobody is listening.
_embedding_progress: Callable[[int, int], None] | None = None


def set_embedding_progress(
    sink: Callable[[int, int], None] | None,
) -> Callable[[int, int], None] | None:
    """Install ``sink(done, total)``; returns the previous sink so callers can
    restore it. Used as the return value of a context manager's ``__enter__``.
    """
    global _embedding_progress
    previous = _embedding_progress
    _embedding_progress = sink
    return previous


def _cosine(a: list[float], b: list[float]) -> float:
    num = sum(x * y for x, y in zip(a, b))
    da = math.sqrt(sum(x * x for x in a))
    db = math.sqrt(sum(y * y for y in b))
    if da == 0.0 or db == 0.0:
        return 0.0
    return num / (da * db)


def contents_for(texts: list[str]) -> list:
    """One ``Content`` per text -- the shape ``embedContent`` really batches on.

    The REST body is ``{"requests": [{model, content}, ...]}`` and the server
    answers with a parallel ``embeddings`` array. The SDK builds exactly one
    request per ``Content`` handed to ``embed_content``, so N ``Content``
    objects is the batching. Handing it a list of plain strings instead packs
    them all into a *single* ``Content`` with many parts -- one request -- and
    the server answers that with one pooled vector for the lot. That was the
    entire ingest stall: 253 chunks degrading to 253 sequential round-trips.
    """
    from google.genai import types

    return [types.Content(parts=[types.Part.from_text(text=t)]) for t in texts]


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
    """``google-genai`` text embeddings, genuinely batched.

    ``embed_content`` takes an array of sub-requests and answers with a parallel
    array of embeddings, so N texts cost one round-trip rather than N. The SDK
    expresses that as one ``Content`` per text -- see :func:`contents_for` for
    why handing it plain strings instead silently degrades to one pooled vector
    per call, which is what made a 253-chunk ingest take minutes of pure
    latency on the free tier's 100 requests/minute.

    Imported lazily so a missing key or SDK never breaks module import.
    """

    #: Texts per ``embed_content`` call. Verified against the live API: a batch
    #: of 64 returned exactly 64 vectors of width 3072. 100 also returned 100
    #: vectors but is left unused -- it is exactly the free tier's 100 req/min
    #: quota, so one full batch can trip the very limit the backoff then has to
    #: wait out. 64 keeps a 253-chunk ingest at four requests and leaves
    #: headroom inside any single quota window.
    BATCH_SIZE = 64

    #: Width every returned vector must have. A vector of another width is not
    #: a slow answer, it is an unindexable one (see :mod:`app.config`).
    EMBED_WIDTH = GEMINI_EMBED_DIM

    #: Attempts and backoff tuned to *this* quota, which measures requests per
    #: minute. Unchanged by the batching work: a 429 mid-batch must retry the
    #: whole batch with the same guarantees a per-text call had.
    #:
    #: The quota error asks for ~23-60s, so a 2s base would burn all attempts
    #: inside a single quota window and fail a pass that had already cost six
    #: minutes. 15s x 8 attempts spans ~4 minutes of quota refill, which is
    #: enough for the whole write.
    EMBED_MAX_ATTEMPTS = 8
    EMBED_BACKOFF_S = 15.0
    EMBED_BACKOFF_CAP_S = 60.0

    #: Class-level defaults so an instance built via ``__new__`` (as the
    #: quota tests do) still has them.
    _sink = None
    _order_checked = False

    def __init__(self, model: str, api_key: str, *, sink=None) -> None:
        from google import genai

        self._client = genai.Client(api_key=api_key)
        self._model = model
        self.dim = GEMINI_EMBED_DIM
        self._sink = sink
        #: One ordering self-check per instance, not per call: it costs three
        #: requests and is worth paying once to know the batch is trustworthy.
        self._order_checked = False

    # -- batching -------------------------------------------------------

    def _embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts, one HTTP call per :data:`BATCH_SIZE`, order preserved.

        Order is the load-bearing property here: the store zips these vectors
        against chunks positionally, so output ``i`` must be text ``i``. Three
        things enforce that, because each catches a failure the others miss:

        1. ``contents_for`` sends N ``Content`` objects, so the server is asked
           for N vectors and returns them positionally by contract.
        2. The returned count is asserted against the count *sent*. A short
           response raises :class:`EmbeddingAlignmentError`; it is never
           truncated or zipped short.
        3. One ordering self-check per instance (see
           :meth:`_verify_order_once`) proves the returned order matches
           per-text order for real, on this model, before a single chunk is
           indexed.

        The "1 vector for N texts" pooled regression is still handled, and
        still falls back to per-text rather than mis-associating.
        """
        if not texts:
            return []
        self._verify_order_once(len(texts))
        self._progress(0, len(texts))
        out: list[list[float]] = []
        for start in range(0, len(texts), self.BATCH_SIZE):
            batch = texts[start : start + self.BATCH_SIZE]
            out.extend(self._embed_batch_checked(batch, offset=start))
            self._progress(len(out), len(texts))
        return out

    def _verify_order_once(self, n_texts: int) -> None:
        """Prove once per instance that a batch's ``i``-th vector is text ``i``.

        Sends two short texts as a batch, then each alone, and compares by
        cosine. If the server ever reorders or shuffles a batch response, the
        positional zip downstream is wrong for every chunk in the ingest --
        silently, because a wrong-but-plausible embedding raises nothing. So it
        is checked once against the real model rather than assumed.

        Skipped when there is nothing to compare (a single-text query) and when
        already done. Raises :class:`EmbeddingAlignmentError` on disagreement.
        """
        if self._order_checked or n_texts < 2:
            self._order_checked = True
            return
        self._order_checked = True
        probe = ("order probe: derivative of a quadratic", "order probe: integral of a cubic")
        batched = self._embed_batch(probe)
        if len(batched) != len(probe):
            # Pooled, not mis-ordered. The fallback path owns this case; the
            # self-check has nothing to conclude.
            log.info(
                "embed_content pooled %d vector(s) for the %d-text order probe; "
                "per-text fallback will apply",
                len(batched), len(probe),
            )
            return
        for idx, text in enumerate(probe):
            single = self._embed_batch([text])
            if len(single) != 1:
                raise EmbeddingAlignmentError(
                    f"order self-check aborted: single-text embed_content for "
                    f"{text!r} returned {len(single)} vector(s)"
                )
            score = _cosine(batched[idx], single[0])
            if score < 0.90:
                raise EmbeddingAlignmentError(
                    f"embed_content batch order does not match per-text order: "
                    f"batched[{idx}] for {text!r} has cosine {score:.4f} against "
                    f"its own single-text embedding (expected ~1.0). Refusing to "
                    f"index -- a positional zip here would attach the wrong "
                    f"vector to the wrong chunk."
                )

    def _embed_batch_checked(self, batch: list[str], *, offset: int = 0) -> list[list[float]]:
        """Embed one batch and refuse to return anything mis-aligned.

        Count mismatch splits two ways:

        * exactly 1 vector for N>1 texts -- the pooled regression. Falls back to
          one call per text, which is slow but provably each text's own vector.
        * any other mismatch -- a short or over-long response. There is no safe
          way to pair those up, so it raises rather than truncating and letting
          every later chunk slide one position out of register.
        """
        vectors = self._embed_batch(batch)
        got, want = len(vectors), len(batch)
        if got == want:
            return vectors
        if want > 1 and got == 1:
            log.warning(
                "gemini embed_content pooled %d vector for %d text(s) at "
                "offset %d; falling back to per-text embedding",
                got, want, offset,
            )
            return [self._embed_batch([t])[0] for t in batch]
        raise EmbeddingAlignmentError(
            f"gemini embed_content returned {got} vector(s) for {want} text(s) "
            f"(batch starting at offset {offset}). Refusing to index: pairing "
            f"them positionally would attach wrong vectors to wrong chunks."
        )

    def _progress(self, done: int, total: int) -> None:
        sink = self._sink or _embedding_progress
        if sink is not None:
            try:
                sink(done, total)
            except Exception:  # progress must never fail an ingest
                log.debug("embedding progress sink raised; ignoring", exc_info=True)

    def _embed_once(self, batch: list[str]) -> list[list[float]]:
        """One HTTP call. N ``Content`` objects in, N embeddings out."""
        resp = self._client.models.embed_content(
            model=self._model, contents=contents_for(batch)
        )
        return [list(item.values) for item in (resp.embeddings or [])]

    def _embed_batch(self, batch: list[str]) -> list[list[float]]:
        """Embed one batch, surviving the free tier's per-minute embedding cap.

        A 655s lecture indexes ~250 chunks. Under the old one-request-per-text
        path that was ~250 requests against a 100 requests/minute free-tier
        quota -- and the chunk write is the *last* step of an ingest that has
        already spent minutes transcribing and describing. An unretried 429
        there throws away the entire pass and fails the job, so the
        wait-and-retry is bounded and shared with the describe loop.

        The retry wraps the *whole batch*, not one text: a 429 is a quota
        window, so re-sending a single member of a failed batch would leave the
        rest of that batch's texts unembedded and silently short-index them.
        Backoff semantics are :mod:`app.ingestion.backoff`'s, unchanged.
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


def get_default_embedder(*, sink=None):
    """Ingestion-side embedder. Gemini only when a key is configured.

    ``sink`` receives ``(embedded, total)`` after each embedding batch. It is
    accepted by both providers so a caller never has to know which one it got;
    the hashing embedder ignores it.
    """
    from app.config import settings

    if settings.embedding_api_key:
        return GeminiEmbedder(
            settings.embedding_model, settings.embedding_api_key, sink=sink
        )
    return HashingEmbedder()


def get_embedder():
    """Retrieval-side embedder. Same choice as ingestion -- see config docstring."""
    return get_default_embedder()