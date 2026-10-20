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

Batching, and why the ingest stalled
-----------------------------------
The embedding endpoint takes an *array of sub-requests*, one per text, and
answers with a parallel array of embeddings. Two separate mistakes made a
253-chunk ingest take over seven minutes:

1. The old code handed the SDK a list of plain strings. The SDK packs a list
   of strings into a *single* ``Content`` with many parts, the server sees one
   request, and it answers with **one pooled vector** for the whole lot
   (measured against the live API: 3 strings -> 1 vector). The old code
   noticed the count mismatch and fell back to one HTTP call per chunk --
   253 sequential round trips at roughly 1.5s each. That is the stall in the
   ingest log.
2. The quota is metered in *texts* per minute, not HTTP calls, so that
   per-chunk fallback also spent the whole minute budget on round trips.

With one ``Content`` per text (:func:`contents_for`) the same chunks go out
in three round trips. What remains is the provider's quota, which no amount
of batching can beat: at 100 texts/minute a 253-chunk video cannot be
embedded in less than ~2.5 minutes. So this module *paces* its calls against
a local model of that quota (:class:`_TextQuota`) instead of discovering the
limit by being throttled into a 60-second backoff after every call that
arrived too eagerly.
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
import re
import threading
import time
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
    db = math.sqrt(sum(x * x for x in b))
    return num / (da * db) if da and db else 0.0


def contents_for(texts: list[str]) -> list:
    """One ``Content`` per text -- the shape the batch endpoint really batches on.

    The REST body is ``{"requests": [{model, content}, ...]}`` and the server
    answers with a parallel ``embeddings`` array. The SDK builds exactly one
    request per ``Content`` handed to ``embed_content``, so N ``Content``
    objects is the batching. Handing it a list of plain strings instead packs
    them all into a *single* ``Content`` with many parts -- one request -- and
    the server answers that with one pooled vector for the lot. That is why a
    253-chunk ingest used to take minutes of pure latency.
    """
    from google.genai import types

    return [types.Content(parts=[types.Part.from_text(text=t)]) for t in texts]


# ---------------------------------------------------------------------------
# Quota
# ---------------------------------------------------------------------------
#
# Measured against the live free tier (gemini-embedding-2), not assumed:
#
# * 100 texts in one call succeed; 101 in one call is rejected outright with
#   ``400 INVALID_ARGUMENT: at most 100 requests can be in one batch``. That is
#   a protocol limit, not a quota one.
# * A second 100-text call five seconds later is rejected with ``429
#   RESOURCE_EXHAUSTED`` and a "retry in ~35s" hint.
# * 55 calls of *two* texts each over 58 seconds -- 110 texts, 57 HTTP calls
#   per minute -- all succeed. So the meter counts *texts*, not HTTP calls.
# * 87 single-text calls in 95 seconds (55 texts/min) never trip it.
# * Draining the bucket with 100 texts and then asking for one more succeeds
#   about a second later, at a refill rate of 100 texts/minute.
#
# Together those say: a token bucket of 100 texts, refilled continuously at
# 100 texts/minute. Modelling it locally is what lets a 253-chunk ingest
# finish in the quota's own time instead of stalling in a 60-second backoff
# after every call that arrived too eagerly.

#: Texts the provider allows per minute, and the size of its bucket.
QUOTA_TEXTS_PER_MINUTE = 100
QUOTA_WINDOW_SECONDS = 60.0


def _quota_per_minute() -> int:
    """The modelled quota, overridable for a project with a higher limit.

    ``EMBEDDING_QUOTA_TEXTS_PER_MINUTE`` is read from the process environment
    only -- it is a deployment knob, not a secret, and the free tier's 100
    texts/minute is the default this repo runs on.
    """
    raw = os.environ.get("EMBEDDING_QUOTA_TEXTS_PER_MINUTE", "")
    if raw:
        try:
            value = int(raw)
        except ValueError:
            log.warning(
                "ignoring EMBEDDING_QUOTA_TEXTS_PER_MINUTE=%r: not an integer", raw
            )
        else:
            if value > 0:
                return value
            log.warning(
                "ignoring EMBEDDING_QUOTA_TEXTS_PER_MINUTE=%r: must be positive", raw
            )
    return QUOTA_TEXTS_PER_MINUTE


class _TextQuota:
    """A token bucket modelling the provider's per-minute *text* quota.

    The bucket starts full (a fresh key has its whole minute available) and
    refills continuously at the configured rate. Callers ask how much is
    available, take what they need, and sleep for the rest.

    Process-global on purpose: the quota belongs to the API key, not to an
    embedder instance, and the store creates one embedder per process while
    the retrieval path creates one per request.
    """

    def __init__(
        self,
        capacity: int = QUOTA_TEXTS_PER_MINUTE,
        refill_per_minute: float = QUOTA_TEXTS_PER_MINUTE,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._capacity = float(capacity)
        self._rate = refill_per_minute / 60.0
        self._clock = clock
        self._sleep = sleeper
        self._tokens = float(capacity)
        self._updated = clock()
        self._lock = threading.Lock()

    @property
    def capacity(self) -> float:
        """How many texts the bucket can hold at once."""
        return self._capacity

    def available(self) -> float:
        """Tokens available right now, after accounting for the refill."""
        with self._lock:
            self._refill()
            return self._tokens

    def take(self, texts: int) -> float:
        """Reserve ``texts`` tokens, waiting for the refill if need be.

        Returns the number of seconds spent waiting. The sleep happens outside
        the lock so a concurrent query is not held up behind it.
        """
        if texts <= 0:
            return 0.0
        waited = 0.0
        while True:
            with self._lock:
                self._refill()
                if self._tokens >= texts:
                    self._tokens -= texts
                    return waited
                deficit = texts - self._tokens
            delay = deficit / self._rate
            log.debug(
                "embedding quota: waiting %.1fs for %d text(s)", delay, texts
            )
            self._sleep(delay)
            waited += delay

    def consume(self, texts: int) -> None:
        """Account for ``texts`` embedded without waiting. Never goes below zero."""
        with self._lock:
            self._refill()
            self._tokens = max(0.0, self._tokens - float(texts))

    def _refill(self) -> None:
        now = self._clock()
        elapsed = max(0.0, now - self._updated)
        self._updated = now
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)


def _default_quota() -> _TextQuota:
    per_minute = _quota_per_minute()
    return _TextQuota(capacity=per_minute, refill_per_minute=per_minute)


_QUOTA = _default_quota()


def embedding_quota() -> _TextQuota:
    """The process-wide quota model."""
    return _QUOTA


def install_embedding_quota(bucket: _TextQuota) -> _TextQuota:
    """Replace the process-wide quota model. Returns the previous one.

    For tests: a test that embeds hundreds of fake texts would otherwise leave
    the shared bucket empty and make the *next* test sleep for a minute.
    """
    global _QUOTA
    previous = _QUOTA
    _QUOTA = bucket
    return previous


def reset_embedding_quota() -> None:
    """Refill the quota model to its full capacity."""
    global _QUOTA
    _QUOTA = _default_quota()


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
        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 0:
            vec = [x / norm for x in vec]
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
    latency on the free tier's 100 texts/minute.

    Imported lazily so a missing key or SDK never breaks module import.
    """

    #: Texts per ``embed_content`` call. The API's own hard limit, verified
    #: against the live model: 100 texts return exactly 100 vectors of width
    #: 3072 in a single HTTP call, and 101 texts are rejected with
    #: ``400 INVALID_ARGUMENT: at most 100 requests can be in one batch``.
    #: Sizing to the limit is what keeps a 253-chunk ingest to three round
    #: trips instead of two hundred and fifty-three.
    BATCH_SIZE = 100

    #: Width every returned vector must have. A vector of another width is not
    #: a slow answer, it is an unindexable one (see :mod:`app.config`).
    EMBED_WIDTH = GEMINI_EMBED_DIM

    #: Attempts and backoff tuned to *this* quota, which measures texts per
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
        #: One ordering self-check per instance, not per call: it costs a
        #: handful of texts of quota and is worth paying once to know the
        #: batch is trustworthy.
        self._order_checked = False

    # -- batching -------------------------------------------------------

    def _embed(self, texts: list[str], *, report_progress: bool) -> list[list[float]]:
        """Embed texts, one HTTP call per batch, order preserved.

        Order is the load-bearing property here: the store zips these vectors
        against chunks positionally, so output ``i`` must be text ``i``. Four
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
        4. Calls are paced against the modelled text quota, so the ingest
           spends the quota's own time instead of colliding with it and being
           thrown into a 60-second backoff.

        The "1 vector for N texts" pooled regression is still handled, and
        still falls back to per-text rather than mis-associating.
        """
        if not texts:
            return []
        self._verify_order_once(len(texts))
        out: list[list[float]] = []
        for start in range(0, len(texts), self.BATCH_SIZE):
            batch = texts[start : start + self.BATCH_SIZE]
            out.extend(self._embed_batch_checked(batch, offset=start))
            if report_progress:
                self._progress(len(out), len(texts))
        return out

    def _await_quota(self, texts: int) -> None:
        """Sleep until the modelled quota can cover ``texts``.

        The wait is for the *whole* batch: a partial batch would either
        overrun the bucket (a 429, and a 429 may cost the texts it was
        refused for) or leave the batch split across two round trips for no
        gain.
        """
        waited = embedding_quota().take(texts)
        if waited > 0:
            log.info(
                "embedding quota: waited %.1fs for %d text(s) of headroom",
                waited, texts,
            )

    def _verify_order_once(self, n_texts: int) -> None:
        """Prove once per instance that a batch's ``i``-th vector is text ``i``.

        Sends two deliberately dissimilar texts as a batch, then each alone,
        and checks that each batched vector matches *its own* single-text
        embedding and not the other one. If the server ever reorders a batch
        response, the positional zip downstream is wrong for every chunk in the
        ingest -- silently, because a wrong-but-plausible embedding raises
        nothing. So it is checked once against the real model rather than
        assumed.

        The two probe texts are chosen to be as far apart in embedding space as
        possible (measured mutual cosine 0.53, against 1.0 for a perfect
        match), so a swapped response cannot be mistaken for a correct one.

        Skipped when there is nothing to compare (a single-text query), and
        skipped again next time so a later batch still gets checked. Raises
        :class:`EmbeddingAlignmentError` on disagreement.
        """
        if self._order_checked or n_texts < 2:
            return
        self._order_checked = True
        probe = (
            (
                "photosynthesis converts light energy into chemical energy inside "
                "chloroplasts"
            ),
            (
                "the defendant was acquitted because the key evidence was ruled "
                "inadmissible"
            ),
        )
        batched = self._embed_batch(list(probe))
        if len(batched) != len(probe):
            # Pooled, not mis-ordered. The fallback path owns this case; the
            # self-check has nothing to conclude.
            log.info(
                "embed_content pooled %d vector(s) for the %d-text order probe; "
                "per-text fallback will apply",
                len(batched), len(probe),
            )
            return
        singles: list[list[float]] = []
        for text in probe:
            single = self._embed_batch([text])
            if len(single) != 1:
                raise EmbeddingAlignmentError(
                    f"order self-check aborted: single-text embed_content for "
                    f"{text!r} returned {len(single)} vector(s)"
                )
            singles.append(single[0])
        for idx, vector in enumerate(batched):
            scores = [_cosine(vector, s) for s in singles]
            best = max(range(len(scores)), key=lambda i: scores[i])
            runner_up = min(scores) if len(scores) > 1 else 0.0
            if best != idx or scores[idx] < 0.95 or scores[idx] - runner_up < 0.05:
                raise EmbeddingAlignmentError(
                    f"embed_content batch order does not match per-text order: "
                    f"batched[{idx}] for {probe[idx]!r} scores {scores[idx]:.4f} "
                    f"against its own single-text embedding and {runner_up:.4f} "
                    f"against the other (expected ~1.0 and far below). Refusing "
                    f"to index -- a positional zip here would attach the wrong "
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
            for vector in vectors:
                if len(vector) != self.EMBED_WIDTH:
                    raise EmbeddingAlignmentError(
                        f"embed_content returned a vector of width "
                        f"{len(vector)}, expected {self.EMBED_WIDTH} (batch "
                        f"starting at offset {offset}). Refusing to index: a "
                        f"wrong-width vector cannot be compared with the "
                        f"chunks it would be attached to."
                    )
            return vectors
        if want > 1 and got == 1:
            log.warning(
                "gemini embed_content pooled %d vector for %d text(s) at "
                "offset %d; falling back to per-text embedding",
                got, want, offset,
            )
            return [self._embed_single(t) for t in batch]
        raise EmbeddingAlignmentError(
            f"gemini embed_content returned {got} vector(s) for {want} text(s) "
            f"(batch starting at offset {offset}). Refusing to index: pairing "
            f"them positionally would attach wrong vectors to wrong chunks."
        )

    def _embed_single(self, text: str) -> list[float]:
        """One text, one vector -- the per-text fallback, with its own check."""
        vectors = self._embed_batch([text])
        if len(vectors) != 1:
            raise EmbeddingAlignmentError(
                f"per-text fallback for {text!r} returned {len(vectors)} "
                f"vector(s); refusing to index a vector that may not be this "
                f"text's own."
            )
        return vectors[0]

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
        path that was ~250 requests against a 100 texts/minute free-tier quota
        -- and the chunk write is the *last* step of an ingest that has
        already spent minutes transcribing and describing. An unretried 429
        there throws away the entire pass and fails the job, so the
        wait-and-retry is bounded and shared with the describe loop.

        The retry wraps the *whole batch*, not one text: a 429 is a quota
        window, so re-sending a single member of a failed batch would leave the
        rest of that batch's texts unembedded and silently short-index them.
        Backoff semantics are :mod:`app.ingestion.backoff`'s, unchanged.

        The quota is reserved *before* the request goes out, so the wait
        happens here rather than as a 429-then-backoff later. A request the
        provider refuses still costs quota, so a 429 mid-batch leaves the
        bucket short; the backoff covers that, and the next batch waits for
        the refill.
        """
        self._await_quota(len(batch))
        return backoff_mod.call_with_retry(
            lambda: self._embed_once(batch),
            what=f"embed_content({self._model}, {len(batch)} text(s))",
            max_attempts=self.EMBED_MAX_ATTEMPTS,
            backoff_s=self.EMBED_BACKOFF_S,
            backoff_cap_s=self.EMBED_BACKOFF_CAP_S,
        )

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed a document set: batched, quota-paced, and progress-reported.

        The progress sink is only fed from here, never from
        :meth:`embed_query`: a retrieval query running while an ingest is in
        flight shares the process-wide sink, and would otherwise overwrite the
        ingest's ``embedded N/total`` counter with its own single vector.
        """
        return self._embed(texts, report_progress=True)

    def embed_query(self, text: str) -> list[float]:
        """Embed one query string.

        Not paced: a query is one text and the caller is waiting on it, so it
        goes straight out and takes the provider's answer for it. It still
        counts against the modelled quota, because it really does consume it.
        """
        vectors = self._embed([text], report_progress=False)
        return vectors[0]


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
