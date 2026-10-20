"""Bounded retry with backoff for the free-tier Gemini quotas.

Both ingest-time Gemini calls -- frame description and chunk embedding -- can
hit ``429 RESOURCE_EXHAUSTED``. The failure mode that matters is not the 429
itself: it is that an unhandled rate limit **ends a pass part-way** and the
caller reports success. A 655s lecture needs 21 describe batches and (with its
transcript) a few embedding batches; the free tier allows ~15 requests/min for
generation and 100/min for embeddings, so a burst will always hit the wall
eventually.

So: serialise, wait, and retry a bounded number of times. Then give up and say
so. An ingest path that quietly returns a short index is the defect these two
callers are fixing, not a cost optimisation.
"""

from __future__ import annotations

import logging
import re
import time

log = logging.getLogger(__name__)

#: A 429 whose advised wait is longer than this is a spent quota (e.g. the
#: free tier's per-day cap), not a per-minute window: retrying only keeps the
#: job "running" for minutes before it fails anyway, so stop at once.
LONG_QUOTA_WAIT_S = 600.0

_RETRY_DELAY_RE = re.compile(r"retryDelay'?\"?\s*:\s*'?\"?(\d+(?:\.\d+)?)s")
_QUOTA_ID_RE = re.compile(r"quotaId'?\"?\s*:\s*'?\"?([A-Za-z0-9_-]+)")
_QUOTA_VALUE_RE = re.compile(r"quotaValue'?\"?\s*:\s*'?\"?(\d+)")


class QuotaExhaustedError(RuntimeError):
    """A provider quota is used up for hours; retrying now cannot succeed."""


#: Process-wide note of the last spent quota: ``(until_monotonic, message)``.
#: Lets callers that would otherwise re-queue work every few seconds (the
#: position heartbeat) report the reason instead of burning more requests.
_QUOTA_BLOCK: tuple[float, str] | None = None
#: How long to hold a block when the provider gave no reset time.
_DEFAULT_BLOCK_S = 600.0


def note_quota_block(wait_s: float, message: str) -> None:
    global _QUOTA_BLOCK
    _QUOTA_BLOCK = (time.monotonic() + (wait_s or _DEFAULT_BLOCK_S), message)


def active_quota_block() -> str | None:
    """The spent-quota message while its reset time has not passed."""
    block = _QUOTA_BLOCK
    if block is None or time.monotonic() >= block[0]:
        return None
    return block[1]


def clear_quota_block() -> None:
    global _QUOTA_BLOCK
    _QUOTA_BLOCK = None


def long_quota_wait_s(exc: BaseException) -> float | None:
    """Seconds the provider says to wait, when that marks a spent quota.

    Returns ``None`` for ordinary rate limits (short waits) and for errors
    that carry no wait at all, so those keep the normal retry path.
    """
    text = str(exc)
    per_day = "PerDay" in text
    m = _RETRY_DELAY_RE.search(text)
    wait = float(m.group(1)) if m else None
    if wait is not None and wait > LONG_QUOTA_WAIT_S:
        return wait
    if per_day:
        return wait if wait is not None else 0.0
    return None


def quota_exhausted_message(exc: BaseException, wait_s: float) -> str:
    """One readable line for the job status; no raw provider payload."""
    text = str(exc)
    qid = _QUOTA_ID_RE.search(text)
    qval = _QUOTA_VALUE_RE.search(text)
    quota = qid.group(1) if qid else "provider quota"
    if "Embed" in quota or "embed_content" in text:
        what = "Gemini embedding quota"
    elif "Generate" in quota or "generate_content" in text:
        what = "Gemini request quota"
    else:
        what = "API quota"
    limit = f" (limit {qval.group(1)}, {quota})" if qval else f" ({quota})"
    if wait_s > 0:
        h, rem = divmod(int(wait_s), 3600)
        when = f"It resets in about {h}h {rem // 60}m."
    else:
        when = "It resets daily."
    return f"{what} reached{limit}. {when} Stopped instead of waiting."

#: Default attempts before a call is abandoned.
DEFAULT_MAX_ATTEMPTS = 5

#: Exponential backoff base and ceiling, in seconds.
DEFAULT_BACKOFF_S = 2.0
DEFAULT_BACKOFF_CAP_S = 60.0

#: Substrings that mark an exception as worth retrying rather than as a
#: permanent failure. Matched against ``str(exc)`` because every SDK error type
#: differs; the numeric codes (429/500/503) and the Gemini status strings are
#: both present deliberately.
RETRYABLE_TOKENS = (
    "429",
    "resource_exhausted",
    "resourcelimit",
    "rate limit",
    "ratelimit",
    "overloaded",
    "unavailable",
    "deadline exceeded",
    "deadline_exceeded",
    "internal error",
    "500",
    "502",
    "503",
    "504",
    "timeout",
    "timed out",
    "connection reset",
    "connection aborted",
    "remote end closed",
    "temporarily",
    "try again",
)

_RETRYABLE_CODES = {"429", "500", "502", "503", "504"}
_RETRYABLE_STATUSES = {"RESOURCE_EXHAUSTED", "UNAVAILABLE"}


def is_retryable(exc: BaseException) -> bool:
    """Is this failure worth another attempt?

    Deliberately generous. This runs on an ingest path, not a request path: a
    spurious "retry this" costs seconds of wall clock, while a spurious "give
    up" silently shortens the index and stays invisible until a learner asks a
    question about the last eight minutes of a lecture.
    """
    code = getattr(exc, "code", None)
    if code is not None and str(code) in _RETRYABLE_CODES:
        return True
    if getattr(exc, "status", None) in _RETRYABLE_STATUSES:
        return True
    text = str(exc).lower()
    return any(token in text for token in RETRYABLE_TOKENS)


def backoff_delay(attempt: int, *, base: float = DEFAULT_BACKOFF_S,
                  cap: float = DEFAULT_BACKOFF_CAP_S) -> float:
    """Capped geometric backoff: ``base, 2*base, 4*base, ..."""

    if base <= 0:
        return 0.0
    return min(cap, base * (2 ** max(0, attempt - 1)))


def call_with_retry(
    fn,
    *,
    what: str,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    backoff_s: float = DEFAULT_BACKOFF_S,
    backoff_cap_s: float = DEFAULT_BACKOFF_CAP_S,
    sleep=time.sleep,
):
    """Call ``fn()``, retrying rate limits and transient errors.

    Re-raises the final exception once the attempts are spent. Callers that
    must not fail the whole ingest (the visual describe loop) catch it and
    record the gap instead -- the point is that the caller *decides*, rather
    than the exception silently unwinding the pass.
    """
    attempts = max(1, max_attempts)
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:  # re-raised below, after classification
            long_wait = long_quota_wait_s(exc)
            if long_wait is not None:
                msg = quota_exhausted_message(exc, long_wait)
                log.error("%s: %s", what, msg)
                note_quota_block(long_wait, msg)
                raise QuotaExhaustedError(msg) from exc
            if not is_retryable(exc) or attempt >= attempts:
                log.error(
                    "%s failed after %d attempt(s): %s: %s",
                    what, attempt, type(exc).__name__, exc,
                )
                raise
            delay = backoff_delay(
                attempt, base=backoff_s, cap=backoff_cap_s
            )
            log.warning(
                "%s attempt %d/%d failed (%s: %s); retrying in %.0fs",
                what, attempt, attempts, type(exc).__name__, exc, delay,
            )
            sleep(delay)
    raise AssertionError("unreachable: the loop above always returns or raises")