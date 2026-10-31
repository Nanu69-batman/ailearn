"""HMAC-signed LMS launch verification.

Canonical payload (newline-joined, scope sorted, empty lesson as "")::

    learner_id\\ncourse_id\\nresource_id\\nlesson_id\\nscope_csv\\ncurrent_time_ms\\nissued_at

``signature`` = hex(HMAC-SHA256(lms_launch_secret, canonical)).

Sessions belong to the signed-in user (Google or email); there is no
session token.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from collections.abc import Sequence

log = logging.getLogger(__name__)

MAX_LAUNCH_AGE_MS = 15 * 60 * 1000
MAX_FUTURE_SKEW_MS = 5 * 60 * 1000
DEV_SECRET = "dev-secret"
#: Learner-clock ceiling: server clamps/bounds current_time_ms to [0, 6h].
MAX_CURRENT_TIME_MS = 6 * 3600 * 1000


def canonical_launch_payload(
    learner_id: str,
    course_id: str,
    resource_id: str,
    lesson_id: str | None,
    resource_scope: Sequence[str],
    current_time_ms: int,
    issued_at: int,
) -> str:
    scope_csv = ",".join(sorted(resource_scope))
    return "\n".join(
        [
            learner_id,
            course_id,
            resource_id,
            lesson_id or "",
            scope_csv,
            str(int(current_time_ms)),
            str(int(issued_at)),
        ]
    )


def sign_launch(
    secret: str,
    learner_id: str,
    course_id: str,
    resource_id: str,
    lesson_id: str | None,
    resource_scope: Sequence[str],
    current_time_ms: int,
    issued_at: int,
) -> str:
    payload = canonical_launch_payload(
        learner_id,
        course_id,
        resource_id,
        lesson_id,
        resource_scope,
        current_time_ms,
        issued_at,
    )
    return hmac.new(
        secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def clamp_current_time_ms(value: int) -> int:
    """Clamp a client-supplied timestamp to the server [0, 6h] ceiling."""
    try:
        v = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(v, MAX_CURRENT_TIME_MS))


def normalize_issued_at_ms(issued_at: int) -> int:
    """Accept epoch ms; tolerate epoch seconds for LMS clock convenience."""
    value = int(issued_at)
    if value < 100_000_000_000:  # < ~1973 in ms -> treat as epoch seconds
        return value * 1000
    return value


def current_time_ms_now() -> int:
    return int(time.time() * 1000)


class LaunchAuthError(ValueError):
    """Raised for any launch rejection; router maps to HTTP 401."""


def verify_lms_launch(
    *,
    secret: str,
    learner_id: str,
    course_id: str,
    resource_id: str,
    lesson_id: str | None,
    resource_scope: Sequence[str],
    current_time_ms: int,
    issued_at: int,
    signature: str | None,
    now_ms: int | None = None,
    allow_unsigned: bool = False,
) -> None:
    """Verify signature + freshness. Raises LaunchAuthError on rejection.

    Fail-closed: a missing signature is accepted only when ``secret`` is the
    dev secret AND ``allow_unsigned`` is True (ALLOW_UNSIGNED_LAUNCH). Every
    other unsigned attempt is rejected and logged at error level.
    """
    now = int(now_ms) if now_ms is not None else current_time_ms_now()

    if not signature:
        if secret == DEV_SECRET and allow_unsigned:
            # Explicit dev-only opt-in; still error-level so it stands out.
            log.error("unsigned LMS launch attempt accepted (dev mode)")
            return
        log.error("unsigned LMS launch rejected (missing signature)")
        raise LaunchAuthError("missing signature")

    expected = sign_launch(
        secret,
        learner_id,
        course_id,
        resource_id,
        lesson_id,
        resource_scope,
        current_time_ms,
        issued_at,
    )
    if not hmac.compare_digest(expected, signature):
        raise LaunchAuthError("invalid signature")

    issued_ms = normalize_issued_at_ms(issued_at)
    if now - issued_ms > MAX_LAUNCH_AGE_MS:
        raise LaunchAuthError("stale launch (>15min)")
    if issued_ms - now > MAX_FUTURE_SKEW_MS:
        raise LaunchAuthError("issued_at is in the future")
