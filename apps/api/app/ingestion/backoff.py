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
import time

log = logging.getLogger(__name__)

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