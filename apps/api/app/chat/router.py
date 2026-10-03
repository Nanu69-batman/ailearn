"""HTTP routes for LMS sessions, chat, and message history."""

from __future__ import annotations

import logging
import time

import httpx
from fastapi import APIRouter, HTTPException, Request

from app.chat.launch import (
    DEV_SECRET,
    LaunchAuthError,
    clamp_current_time_ms,
    create_session_token,
    verify_lms_launch,
    verify_session_token,
)
from app.chat.schemas import (
    ChatRequest,
    ChatResponse,
    LmsSessionRequest,
    LmsSessionResponse,
    MessageOut,
    MessagesResponse,
)
from app.chat.service import ChatService, SessionNotFoundError
from app.chat.sessions import message_store, reset_stores, session_store

log = logging.getLogger(__name__)

router = APIRouter()

_service_override: ChatService | None = None

# H3 MVP-minimal fixed-window rate limiter (no new deps): 60 req/min/IP
# applied to /chat + /lms/session. Mutable for tests.
RATE_LIMIT_MAX = 60
RATE_LIMIT_WINDOW_S = 60.0
_rate_buckets: dict[str, list[float]] = {}


def _client_ip(request: Request | None) -> str:
    if request is None or request.client is None:
        return "unknown"
    return request.client.host or "unknown"


def _check_rate_limit(ip: str) -> bool:
    """Return True if allowed; False when the fixed window is exhausted."""
    now = time.monotonic()
    bucket = _rate_buckets.setdefault(ip, [])
    cutoff = now - RATE_LIMIT_WINDOW_S
    del bucket[: sum(1 for ts in bucket if ts <= cutoff)]
    if len(bucket) >= RATE_LIMIT_MAX:
        return False
    bucket.append(now)
    return True


def reset_rate_limits() -> None:
    _rate_buckets.clear()


def _database_url_looks_real(url: str | None) -> bool:
    if not url:
        return False
    lowered = url.lower()
    if "localhost" in lowered or "127.0.0.1" in lowered:
        return False
    # A real Postgres URL carries a host (user:pass@host/db).
    return "@" in url


def get_vector_store():
    """Settings-driven store factory (H4): no network/DB at factory time.

    Returns ``PostgresVectorStore`` (lazy engine: no connection opened here)
    when USE_IN_MEMORY is false and DATABASE_URL looks real; otherwise an
    empty in-memory store. Tests run with USE_IN_MEMORY=true.
    """
    from app.config import settings

    if settings.use_in_memory:
        from app.retrieval.registry import get_shared_store

        return get_shared_store()
    if _database_url_looks_real(settings.database_url):
        from app.retrieval.store import PostgresVectorStore

        return PostgresVectorStore(database_url=settings.database_url)
    from app.retrieval.registry import get_shared_store

    return get_shared_store()


def describe_active_providers() -> dict[str, str]:
    """Provider names for the startup log line (no I/O)."""
    from app.config import settings

    llm = "gemini" if settings.llm_api_key else "stub"
    if settings.use_in_memory or not _database_url_looks_real(settings.database_url):
        store = "in-memory"
    else:
        store = "postgres"
    sessions = (
        "in-memory" if settings.use_in_memory else "in-memory (postgres-backed planned)"
    )
    return {"llm": llm, "store": store, "sessions": sessions}


def get_service() -> ChatService:
    if _service_override is not None:
        return _service_override
    from app.chat.llm import get_llm_provider
    from app.ingestion.embedder import get_embedder

    return ChatService(
        store=get_vector_store(),
        embedder=get_embedder(),
        llm=get_llm_provider(),
        sessions=session_store,
        messages=message_store,
    )


def set_service_override(service: ChatService | None) -> None:
    global _service_override
    _service_override = service


def reset_chat_state() -> None:
    reset_stores()
    reset_rate_limits()
    global _service_override
    _service_override = None


@router.post("/lms/session", response_model=LmsSessionResponse)
def create_lms_session(body: LmsSessionRequest, request: Request) -> LmsSessionResponse:
    from app.config import settings

    if not _check_rate_limit(_client_ip(request)):
        raise HTTPException(status_code=429, detail="rate limit exceeded")
    secret = settings.lms_launch_secret
    try:
        verify_lms_launch(
            secret=secret,
            learner_id=body.learner_id,
            course_id=body.course_id,
            resource_id=body.resource_id,
            lesson_id=body.lesson_id,
            resource_scope=body.resource_scope,
            current_time_ms=body.current_time_ms,
            issued_at=body.issued_at,
            signature=body.signature,
            allow_unsigned=settings.allow_unsigned_launch,
        )
    except LaunchAuthError as exc:
        # M3: log specifics server-side only; clients get a generic detail.
        log.warning("lms launch rejected: %s", str(exc))
        raise HTTPException(status_code=401, detail="invalid launch") from exc
    if not body.signature and secret == DEV_SECRET:
        log.error("unsigned LMS launch accepted (dev-secret mode)")
    session = session_store.create(
        learner_id=body.learner_id,
        course_id=body.course_id,
        resource_id=body.resource_id,
        lesson_id=body.lesson_id,
        resource_scope=list(body.resource_scope),
        current_time_ms=clamp_current_time_ms(body.current_time_ms),
    )
    # Never log session_id at info level; only return the binding token.
    token = create_session_token(secret, session.id, session.learner_id)
    return LmsSessionResponse(
        session_id=session.id,
        learner_id=session.learner_id,
        course_id=session.course_id,
        session_token=token,
    )


def _require_session_token(
    *, secret: str, session_id: str, learner_id: str, token: str | None
) -> None:
    if not verify_session_token(secret, session_id, learner_id, token):
        log.warning("session token verification failed")
        raise HTTPException(status_code=401, detail="invalid launch")


@router.post("/chat", response_model=ChatResponse)
async def post_chat(body: ChatRequest, request: Request) -> ChatResponse:
    from app.config import settings

    if not _check_rate_limit(_client_ip(request)):
        raise HTTPException(status_code=429, detail="rate limit exceeded")
    session = session_store.get(body.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    _require_session_token(
        secret=settings.lms_launch_secret,
        session_id=session.id,
        learner_id=session.learner_id,
        token=body.session_token,
    )
    service = get_service()
    try:
        return await service.chat(
            session_id=body.session_id,
            current_time_ms=clamp_current_time_ms(body.current_time_ms),
            message=body.message,
            mode=body.mode.value,
        )
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
    except HTTPException:
        raise
    except (httpx.HTTPError, ConnectionError, TimeoutError, RuntimeError) as exc:
        # M5: provider/upstream failure -> 502 with redacted detail.
        name = type(exc).__name__
        # M5: log specifics server-side; never leak provider internals.
        log.error("chat provider error: %s", f"{name}: {exc}")
        raise HTTPException(status_code=502, detail="upstream provider error") from exc


@router.get("/sessions/{session_id}/messages", response_model=MessagesResponse)
def get_messages(
    session_id: str, request: Request, session_token: str | None = None
) -> MessagesResponse:
    from app.config import settings

    session = session_store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    token = session_token or request.headers.get("x-session-token")
    _require_session_token(
        secret=settings.lms_launch_secret,
        session_id=session.id,
        learner_id=session.learner_id,
        token=token,
    )
    out = [
        MessageOut(
            role=m.role,
            content=m.content,
            citations=m.citations,  # type: ignore[arg-type]
            created_at=m.created_at,
        )
        for m in message_store.list_for_session(session_id)
    ]
    return MessagesResponse(session_id=session_id, messages=out)
