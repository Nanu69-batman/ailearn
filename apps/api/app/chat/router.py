"""HTTP routes for LMS sessions, chat, and message history."""

from __future__ import annotations

import logging
import time
from typing import Annotated

import httpx
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from app.chat.launch import (
    DEV_SECRET,
    LaunchAuthError,
    clamp_current_time_ms,
    create_session_token,
    verify_lms_launch,
    verify_session_token,
)
from app.chat.schemas import (
    ChatMode,
    ChatRequest,
    ChatResponse,
    LmsSessionRequest,
    LmsSessionResponse,
    MessageOut,
    MessagesResponse,
    ThreadOut,
    ThreadRenameRequest,
    ThreadsResponse,
)
from app.chat.service import ChatService, SessionNotFoundError
from app.chat.sessions import (
    MAIN_THREAD,
    ChatThread,
    current_thread,
    message_store,
    reset_stores,
    session_store,
    thread_store,
    valid_thread_id,
)
from app.ingestion.backoff import QuotaExhaustedError

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

    Both branches now resolve to the Chroma ``PersistentClient``; the
    hand-built ``PostgresVectorStore`` is gone per ADR-002. ``USE_IN_MEMORY``
    no longer selects between two implementations -- it selects between a
    process-local Chroma directory and one under ``data/``, which the
    registry already handles. Creating the client opens (and, on first run,
    writes) a local directory; it makes no network call.

    SAFETY: the gate is applied at query time (``end_ms IS NOT NULL AND
    end_ms <= T``) inside ``ChromaVectorStore.query``'s ``where`` filter --
    see ``app.retrieval`` and ADR-002 section 5.1.
    """
    from app.config import settings

    log.info(
        "vector store: use_in_memory=%s database_url_set=%s",
        settings.use_in_memory,
        _database_url_looks_real(settings.database_url),
    )
    from app.retrieval.registry import get_shared_store

    return get_shared_store()


def describe_active_providers() -> dict[str, str]:
    """Provider names for the startup log line (no I/O)."""
    from app.config import settings

    llm = "gemini" if settings.llm_api_key else "stub"
    if settings.openrouter_api_key:
        llm = f"agent:{settings.openrouter_model} (fallback {llm})"
    store = "chroma"
    sessions = (
        "in-memory" if settings.use_in_memory else "in-memory (postgres-backed planned)"
    )
    return {"llm": llm, "store": store, "sessions": sessions}


def get_service() -> ChatService:
    if _service_override is not None:
        return _service_override
    from app.chat.agent import get_agent
    from app.chat.llm import get_llm_provider
    from app.ingestion.embedder import get_embedder

    return ChatService(
        store=get_vector_store(),
        embedder=get_embedder(),
        llm=get_llm_provider(),
        sessions=session_store,
        messages=message_store,
        agent=get_agent(),
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
    _use_thread(session.id, body.thread_id)
    service = get_service()
    try:
        return await service.chat(
            session_id=body.session_id,
            current_time_ms=clamp_current_time_ms(body.current_time_ms),
            message=body.message,
            mode=body.mode.value,
            # The open material. Retrieval reads the grant INTERSECT this one
            # (gated at the playhead), plus whatever was marked done.
            focus_resource_id=body.video_id,
            completed_resource_ids=body.completed_resource_ids,
        )
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
    except HTTPException:
        raise
    except QuotaExhaustedError:
        # A spent quota is a RuntimeError too, but it must reach the app's
        # 503 handler with its readable reason, not a generic 502.
        raise
    except (httpx.HTTPError, ConnectionError, TimeoutError, RuntimeError) as exc:
        # M5: provider/upstream failure -> 502 with redacted detail.
        name = type(exc).__name__
        # M5: log specifics server-side; never leak provider internals.
        log.error("chat provider error: %s", f"{name}: {exc}")
        raise HTTPException(status_code=502, detail="upstream provider error") from exc


def _resolve_thread(session_id: str, thread_id: str | None) -> str:
    """A chat id that exists in this session, or 404. None means "main"."""
    tid = thread_id or MAIN_THREAD
    if not valid_thread_id(tid) or thread_store.get(session_id, tid) is None:
        raise HTTPException(status_code=404, detail="chat not found")
    return tid


def _use_thread(session_id: str, thread_id: str | None) -> None:
    """Point this request's message history at one chat of the session.

    Called only after the session token has been verified. The chat service
    is unaware of threads; the message store reads ``current_thread``.
    """
    current_thread.set(_resolve_thread(session_id, thread_id))


def _authed_session(session_id: str, request: Request, session_token: str | None):
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
    return session


def _thread_out(session_id: str, t: ChatThread) -> ThreadOut:
    msgs = message_store.list_for_session(session_id, t.id)
    first_q = next((m.content for m in msgs if m.role == "user"), None)
    derived = " ".join(first_q.split())[:60] if first_q else None
    return ThreadOut(
        id=t.id,
        title=t.title or derived,
        renamed=t.title is not None,
        created_at=t.created_at,
        updated_at=msgs[-1].created_at if msgs else t.created_at,
        message_count=len(msgs),
    )


@router.get("/sessions/{session_id}/threads", response_model=ThreadsResponse)
def list_threads(
    session_id: str, request: Request, session_token: str | None = None
) -> ThreadsResponse:
    session = _authed_session(session_id, request, session_token)
    threads = [
        _thread_out(session.id, t)
        for t in thread_store.list(session.id, session_created_at=session.created_at)
    ]
    threads.sort(key=lambda t: t.updated_at, reverse=True)
    return ThreadsResponse(session_id=session.id, threads=threads)


@router.post("/sessions/{session_id}/threads", response_model=ThreadOut)
def create_thread(
    session_id: str, request: Request, session_token: str | None = None
) -> ThreadOut:
    session = _authed_session(session_id, request, session_token)
    try:
        t = thread_store.create(session.id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _thread_out(session.id, t)


@router.patch("/sessions/{session_id}/threads/{thread_id}", response_model=ThreadOut)
def rename_thread(
    session_id: str,
    thread_id: str,
    body: ThreadRenameRequest,
    request: Request,
    session_token: str | None = None,
) -> ThreadOut:
    session = _authed_session(session_id, request, session_token)
    tid = _resolve_thread(session.id, thread_id)
    t = thread_store.rename(session.id, tid, body.title)
    if t is None:
        raise HTTPException(status_code=404, detail="chat not found")
    return _thread_out(session.id, t)


@router.delete("/sessions/{session_id}/threads/{thread_id}")
def delete_thread(
    session_id: str, thread_id: str, request: Request, session_token: str | None = None
) -> dict:
    """Delete a chat and its messages. The main chat is emptied, not removed."""
    session = _authed_session(session_id, request, session_token)
    tid = _resolve_thread(session.id, thread_id)
    thread_store.delete(session.id, tid)
    return {"deleted": True, "thread_id": tid, "emptied": tid == MAIN_THREAD}


@router.get("/sessions/{session_id}/messages", response_model=MessagesResponse)
def get_messages(
    session_id: str,
    request: Request,
    session_token: str | None = None,
    thread_id: str | None = None,
) -> MessagesResponse:
    session = _authed_session(session_id, request, session_token)
    tid = _resolve_thread(session.id, thread_id)
    out = [
        MessageOut(
            role=m.role,
            content=m.content,
            citations=m.citations,  # type: ignore[arg-type]
            created_at=m.created_at,
        )
        for m in message_store.list_for_session(session_id, tid)
    ]
    return MessagesResponse(session_id=session_id, messages=out, thread_id=tid)


# ---------------------------------------------------------------------------
# POST /chat/live-frame -- answer about the frame currently on the learner's
# screen. ADDITIVE: /chat is untouched and remains the plain-JSON path.
#
# Both triggers are honoured:
#   * the explicit player button  -> force_frame=true, regardless of wording
#   * a question that clearly asks about the current frame -> is_frame_question()
#
# The honest-failure ordering below is the point of this route. Nothing is
# inferred, clamped or substituted:
#   1. session + token (the same gate /chat has)
#   2. the resource must be inside the session's own grant
#   3. does this material have a frame at all?  -> pdf/pptx/image degrade, no
#      vision call, and the answer says so
#   4. server-side duration, then T is VALIDATED against it -> 422, never clamped
#   5. describe the capture (cached per resource-second) -> 422 if unreadable
# ---------------------------------------------------------------------------

#: Said in the answer when the material has no frame. Never fabricated away: a
#: learner who asked "what am I looking at?" about a PDF must be told there was
#: no screen to look at, and that the answer below is the document's own text.
DOCUMENT_NO_FRAME_TEXT = (
    "This material is a document rather than a video, so there is no frame to "
    "capture and nothing was read from a screen. What follows is answered from "
    "the document's own page and slide text."
)

#: Largest capture accepted. A canvas JPEG of a 4K video frame is a few hundred
#: KB; 8MB is generous and bounds a hostile upload before it reaches the vision
#: call. Mirrors the spirit of ``app.ingestion.jobs.MAX_UPLOAD_BYTES``.
MAX_LIVE_FRAME_BYTES = 8 * 1024 * 1024

#: Accepted capture mime types. ``app.ingestion.visual._gemini_describe`` labels
#: every frame ``image/jpeg``, so anything else is rejected here rather than
#: being relabelled into a lie about what the model was shown.
LIVE_FRAME_MIME_TYPES = frozenset({"image/jpeg", "image/jpg"})


def _validate_mode(raw: str) -> str:
    try:
        return ChatMode(raw).value
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail=f"unknown chat mode: {raw!r}"
        ) from exc


@router.post("/chat/live-frame", response_model=ChatResponse)
async def post_chat_live_frame(
    request: Request,
    session_id: Annotated[str, Form()],
    current_time_ms: Annotated[int, Form()],
    message: Annotated[str, Form(min_length=1, max_length=4000)],
    frame: Annotated[UploadFile, File()],
    mode: Annotated[str, Form()] = ChatMode.qa.value,
    video_id: Annotated[str | None, Form()] = None,
    session_token: Annotated[str | None, Form()] = None,
    force_frame: Annotated[bool, Form()] = False,
    thread_id: Annotated[str | None, Form(max_length=64)] = None,
    completed_resource_ids: Annotated[list[str] | None, Form()] = None,
) -> ChatResponse:
    from app.config import settings
    from app.ingestion.liveframe import (
        LiveFrame,
        LiveFrameRejected,
        LiveFrameUnavailable,
        describe_live_frame,
        resolve_resource_duration_ms,
        resource_has_frame,
        validate_frame_time_ms,
    )
    from app.retrieval.recency import is_frame_question

    if not _check_rate_limit(_client_ip(request)):
        raise HTTPException(status_code=429, detail="rate limit exceeded")
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
    _use_thread(session.id, thread_id)
    chat_mode = _validate_mode(mode)

    resource_id = video_id or session.resource_id
    # The frame is evidence ABOUT a specific resource, so the resource must be
    # inside the session's own grant. Intersected, never substituted: a focus
    # outside the grant is refused here rather than quietly answering about the
    # session's default resource instead.
    if resource_id not in set(session.resource_scope or []):
        raise HTTPException(
            status_code=403,
            detail="this session is not authorised for that resource",
        )

    # (3) Does this material have a frame? Fail-closed on an unknown kind: no
    #     vision call is made for a document, and nothing is invented for one.
    source_type = None
    try:
        source_type = get_vector_store().resource_source_type(resource_id)
    except AttributeError:  # a store double without the pass-through
        source_type = None
    if not resource_has_frame(source_type):
        log.info(
            "live frame: %s is source_type=%r -- no frame exists; degrading to "
            "the document's own text",
            resource_id,
            source_type or "unknown",
        )
        service = get_service()
        return await service.chat(
            session_id=session.id,
            current_time_ms=current_time_ms,
            message=message,
            mode=chat_mode,
            focus_resource_id=video_id,
            completed_resource_ids=completed_resource_ids or [],
            live_frame=None,
            no_frame_note=DOCUMENT_NO_FRAME_TEXT,
        )

    # (4) SERVER-SIDE duration, then validate T against it. The client sends no
    #     duration at all, so there is nothing for it to lie about; an
    #     out-of-range T is refused, never clamped onto a neighbouring moment.
    duration_ms = resolve_resource_duration_ms(resource_id)
    try:
        at_ms = validate_frame_time_ms(
            at_ms=current_time_ms, duration_ms=duration_ms
        )
    except LiveFrameRejected as exc:
        log.warning("live frame rejected: %s", exc)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except LiveFrameUnavailable as exc:
        log.error("live frame: %s", exc)
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Both triggers, and nothing else turns this on. A frame arriving with
    # neither is a client/server disagreement about the classifier, which is a
    # bug worth surfacing rather than silently ignoring.
    if not (force_frame or is_frame_question(message)):
        raise HTTPException(
            status_code=422,
            detail="this question is not about the current frame; send it to "
            "/chat, or press the 'about this frame' button to force a capture",
        )

    payload = await frame.read(MAX_LIVE_FRAME_BYTES + 1)
    if not payload:
        raise HTTPException(status_code=422, detail="captured frame is empty")
    if len(payload) > MAX_LIVE_FRAME_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"captured frame exceeds {MAX_LIVE_FRAME_BYTES // (1024*1024)}MB",
        )
    mime = (frame.content_type or "").split(";")[0].strip().lower()
    if mime and mime not in LIVE_FRAME_MIME_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"captured frame must be JPEG (got {mime!r})",
        )

    try:
        description = describe_live_frame(
            payload, resource_id=resource_id, at_ms=at_ms
        )
    except LiveFrameUnavailable as exc:
        # An unreadable frame is an honest failure, never a fallback description
        # and never a silent drop to the nearest pre-indexed frame.
        log.warning("live frame unreadable for %s at %sms: %s", resource_id, at_ms, exc)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except QuotaExhaustedError:
        # A spent quota is a RuntimeError too, but it must reach the app's
        # 503 handler with its readable reason, not a generic 502.
        raise
    except (httpx.HTTPError, ConnectionError, TimeoutError, RuntimeError) as exc:
        log.error("live frame vision error: %s: %s", type(exc).__name__, exc)
        raise HTTPException(status_code=502, detail="upstream provider error") from exc

    live = LiveFrame(
        resource_id=resource_id,
        course_id=session.course_id,
        at_ms=at_ms,
        text=description,
        video_id=None,
    )
    service = get_service()
    return await service.chat(
        session_id=session.id,
        current_time_ms=at_ms,
        message=message,
        mode=chat_mode,
        focus_resource_id=video_id,
        completed_resource_ids=completed_resource_ids or [],
        live_frame=live,
    )
