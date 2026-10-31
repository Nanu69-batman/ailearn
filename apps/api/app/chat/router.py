"""HTTP routes for LMS sessions, chat, and message history."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse

from app.auth import AuthUser, get_current_user
from app.chat.launch import (
    DEV_SECRET,
    LaunchAuthError,
    clamp_current_time_ms,
    verify_lms_launch,
)
from app.chat.quiz import (
    GradeRequest,
    GradeResponse,
    QuizNoteRequest,
    QuizRequest,
    QuizResponse,
    quiz_note_lines,
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
        llm = f"agent:openrouter by mode (fallback {llm})"
    store = "chroma"
    sessions = (
        "in-memory" if settings.use_in_memory else "in-memory (postgres-backed planned)"
    )
    return {"llm": llm, "store": store, "sessions": sessions}


def get_service() -> ChatService:
    if _service_override is not None:
        return _service_override
    from app.chat.agent import get_agent
    from app.chat.intent_model import get_intent_router
    from app.chat.llm import get_llm_provider
    from app.ingestion.embedder import get_embedder

    return ChatService(
        store=get_vector_store(),
        embedder=get_embedder(),
        llm=get_llm_provider(),
        sessions=session_store,
        messages=message_store,
        agent=get_agent(),
        intent_router=get_intent_router(),
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
def create_lms_session(
    body: LmsSessionRequest,
    request: Request,
    user: Annotated[AuthUser | None, Depends(get_current_user)],
) -> LmsSessionResponse:
    from app.config import settings

    # Google or email sign-in is the only way in: the session is the user's.
    if user is None:
        raise HTTPException(status_code=401, detail="authentication required")

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
    # A signed-in learner is always themselves: the body value is ignored.
    learner_id = user.id
    session = session_store.create(
        learner_id=learner_id,
        course_id=body.course_id,
        resource_id=body.resource_id,
        lesson_id=body.lesson_id,
        resource_scope=list(body.resource_scope),
        current_time_ms=clamp_current_time_ms(body.current_time_ms),
        owner_id=user.id,
    )
    return LmsSessionResponse(
        session_id=session.id,
        learner_id=session.learner_id,
        course_id=session.course_id,
    )


def _check_owner(session, request: Request) -> None:
    """401 when signed out; 404 unless the signed-in user owns the session."""
    user = get_current_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="sign in required")
    if getattr(session, "owner_id", None) != user.id:
        raise HTTPException(status_code=404, detail="session not found")


def _authorize_chat(body: ChatRequest, request: Request) -> None:
    """Rate limit, session and token checks shared by both chat routes."""

    if not _check_rate_limit(_client_ip(request)):
        raise HTTPException(status_code=429, detail="rate limit exceeded")
    session = session_store.get(body.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    _check_owner(session, request)
    _use_thread(session.id, body.thread_id)


async def _answer(body: ChatRequest, *, voice: bool = False) -> ChatResponse:
    """Run one question through the chat service.

    ``voice`` asks for a short spoken-style answer on the fast (non-agent)
    pipeline; the gate, citation validation and refusals are the same.
    """
    from app.chat.attachments import prompt_note
    from app.chat.prompts import style_for, style_note

    service = get_service()
    # Images attached to this chat are context for every answer in it.
    images = prompt_note(body.session_id, _resolve_thread(body.session_id, body.thread_id))
    style = "\n\n".join(p for p in (style_for(body.personalization, voice=voice), images) if p)
    note = style_note.set(style)
    try:
        resp = await service.chat(
            session_id=body.session_id,
            current_time_ms=clamp_current_time_ms(body.current_time_ms),
            message=body.message,
            mode=body.mode.value,
            # The open material. Retrieval reads the grant INTERSECT this one
            # (gated at the playhead), plus whatever was marked done.
            focus_resource_id=body.video_id,
            completed_resource_ids=body.completed_resource_ids,
            # Only sent when narrowing, so services without the option still work.
            **(
                {"scope_resource_ids": body.scope_resource_ids}
                if body.scope_resource_ids is not None
                else {}
            ),
            **({"general": True} if body.general else {}),
            **({"fast": True} if voice else {}),
        )
        if body.suggest_resource_ids and (resp.refused or resp.mode == "supplemental"):
            resp.suggested_resource_ids = await _suggest(body)
        return resp
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
    finally:
        style_note.reset(note)


async def _suggest(body: ChatRequest) -> list[str]:
    """Materials outside this chat's reach that would answer the question."""
    from app.chat.suggest import suggest_candidates, suggest_materials

    session = session_store.get(body.session_id)
    if session is None:
        return []
    readable = body.scope_resource_ids if body.scope_resource_ids is not None else session.resource_scope
    candidates = suggest_candidates(session.resource_scope, readable, body.suggest_resource_ids)
    return await suggest_materials(get_service(), session, body.message, candidates)


@router.post("/chat", response_model=ChatResponse)
async def post_chat(body: ChatRequest, request: Request) -> ChatResponse:
    _authorize_chat(body, request)
    return await _answer(body)


_TITLE_SYSTEM = (
    "You name a tutoring chat from the learner's first message. Reply with the "
    "title only: 2 to 6 words, Title Case, no quotes, no trailing punctuation. "
    "Keep the language the learner wrote in."
)
#: After the answer is done, how much longer the title may take.
TITLE_WAIT_S = 4.0


def _needs_title(session_id: str, thread_id: str | None) -> bool:
    """A chat's first question, in a chat nobody has named yet."""
    tid = _resolve_thread(session_id, thread_id)
    t = thread_store.get(session_id, tid)
    if t is None or t.title:
        return False
    return not any(m.role == "user" for m in message_store.list_for_session(session_id, tid))


async def _auto_title(session_id: str, thread_id: str | None, message: str) -> str | None:
    """Name the chat from its first question (one short model call). Never raises."""
    from app.chat.llm import DeterministicStubLLM

    llm = getattr(get_service(), "_llm", None)
    # No real model (tests, offline): the list keeps showing the first question.
    if llm is None or isinstance(llm, DeterministicStubLLM):
        return None
    try:
        result = await llm.complete(
            system=_TITLE_SYSTEM,
            user_prompt="First message:\n" + message[:1000],
            passages=[],
            mode="title",
        )
    except Exception as exc:  # noqa: BLE001 - a missing title must not fail the chat
        log.warning("chat title failed: %s", type(exc).__name__)
        return None
    title = " ".join((result.text or "").split()).strip().strip("\"'*#.").strip()
    if not title:
        return None
    t = thread_store.rename(session_id, _resolve_thread(session_id, thread_id), title[:60])
    return t.title if t is not None else None


def _start_title(body: ChatRequest) -> asyncio.Task | None:
    if not body.message.strip() or not _needs_title(body.session_id, body.thread_id):
        return None
    task = asyncio.create_task(_auto_title(body.session_id, body.thread_id, body.message))
    task.add_done_callback(lambda t: t.cancelled() or t.exception())
    return task


async def _title_of(task: asyncio.Task | None) -> str | None:
    if task is None:
        return None
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout=TITLE_WAIT_S)
    except (TimeoutError, asyncio.CancelledError):
        return None


def _sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@router.post("/chat/stream")
async def post_chat_stream(body: ChatRequest, request: Request) -> StreamingResponse:
    """``POST /chat`` as Server-Sent Events, so the answer appears as it is written.

    Auth failures are still plain HTTP errors (checked before streaming starts).
    Then: ``status`` events (``{"text"}``, what the tutor is doing), ``text``
    events (``{"text"}``, the answer so far, markers stripped; empty means start
    over), and exactly one final ``done`` (the validated :class:`ChatResponse`,
    which replaces the preview) or ``error`` (``{"status", "detail"}``).
    """
    from app.chat import streaming

    _authorize_chat(body, request)
    # Named alongside the first answer, so the title costs no extra wait.
    title_task = _start_title(body)
    sink = streaming.StreamSink()
    bound = streaming.bind(sink)
    try:
        # The task copies this context: the sink and the chat thread.
        task = asyncio.create_task(_answer(body))
    finally:
        streaming.unbind(bound)
    # If the learner closes the tab the answer still finishes and is saved to
    # the chat history; this only stops an unread exception from being logged.
    task.add_done_callback(lambda t: t.cancelled() or t.exception())

    async def events():
        yield _sse("status", {"text": "Thinking"})
        while True:
            getter = asyncio.ensure_future(sink.queue.get())
            await asyncio.wait({getter, task}, return_when=asyncio.FIRST_COMPLETED)
            if getter.done():
                kind, value = getter.result()
                yield _sse(kind, {"text": value})
                continue
            getter.cancel()
            break
        while not sink.queue.empty():
            kind, value = sink.queue.get_nowait()
            yield _sse(kind, {"text": value})
        exc = task.exception()
        if exc is None:
            done = task.result().model_dump(mode="json")
            title = await _title_of(title_task)
            if title:
                done["thread_title"] = title
            yield _sse("done", done)
        elif isinstance(exc, HTTPException):
            yield _sse("error", {"status": exc.status_code, "detail": exc.detail})
        elif isinstance(exc, QuotaExhaustedError):
            yield _sse("error", {"status": 503, "detail": str(exc)})
        else:
            log.error("chat stream failed: %s", type(exc).__name__)
            yield _sse("error", {"status": 500, "detail": "internal error"})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        # No proxy buffering, or the "stream" arrives all at once at the end.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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


def _authed_session(session_id: str, request: Request):

    session = session_store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    _check_owner(session, request)
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
    session_id: str, request: Request
) -> ThreadsResponse:
    session = _authed_session(session_id, request)
    threads = [
        _thread_out(session.id, t)
        for t in thread_store.list(session.id, session_created_at=session.created_at)
    ]
    threads.sort(key=lambda t: t.updated_at, reverse=True)
    return ThreadsResponse(session_id=session.id, threads=threads)


@router.post("/sessions/{session_id}/threads", response_model=ThreadOut)
def create_thread(
    session_id: str, request: Request
) -> ThreadOut:
    session = _authed_session(session_id, request)
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
) -> ThreadOut:
    session = _authed_session(session_id, request)
    tid = _resolve_thread(session.id, thread_id)
    t = thread_store.rename(session.id, tid, body.title)
    if t is None:
        raise HTTPException(status_code=404, detail="chat not found")
    return _thread_out(session.id, t)


@router.delete("/sessions/{session_id}/threads/{thread_id}")
def delete_thread(
    session_id: str, thread_id: str, request: Request
) -> dict:
    """Delete a chat and its messages. The main chat is emptied, not removed."""
    session = _authed_session(session_id, request)
    tid = _resolve_thread(session.id, thread_id)
    thread_store.delete(session.id, tid)
    return {"deleted": True, "thread_id": tid, "emptied": tid == MAIN_THREAD}


@router.get("/sessions/{session_id}/messages", response_model=MessagesResponse)
def get_messages(
    session_id: str,
    request: Request,
    thread_id: str | None = None,
) -> MessagesResponse:
    session = _authed_session(session_id, request)
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
    force_frame: Annotated[bool, Form()] = False,
    thread_id: Annotated[str | None, Form(max_length=64)] = None,
    completed_resource_ids: Annotated[list[str] | None, Form()] = None,
) -> ChatResponse:
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
    _check_owner(session, request)
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
        # Blocking vision call (with retries): off the event loop.
        description = await asyncio.to_thread(
            describe_live_frame, payload, resource_id=resource_id, at_ms=at_ms
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


# --- practice quizzes (app.chat.quiz) ----------------------------------------


def get_quiz_service():
    from app.chat.agent import get_agent
    from app.chat.llm import get_llm_provider
    from app.chat.quiz import QuizService
    from app.ingestion.embedder import get_embedder

    if _service_override is not None:
        svc = _service_override
        return QuizService(
            store=svc._store, embedder=svc._embedder, llm=svc._llm,
            sessions=svc._sessions, agent=svc._agent, messages=svc._messages,
        )
    return QuizService(
        store=get_vector_store(),
        embedder=get_embedder(),
        llm=get_llm_provider(),
        sessions=session_store,
        agent=get_agent(),
        messages=message_store,
    )


def _quiz_upstream_error(exc: Exception) -> HTTPException:
    log.error("quiz provider error: %s", f"{type(exc).__name__}: {exc}")
    return HTTPException(status_code=502, detail="upstream provider error")


@router.post("/quiz", response_model=QuizResponse)
async def post_quiz(body: QuizRequest, request: Request) -> QuizResponse:
    """A practice quiz on the open material up to T, plus materials marked done."""
    if not _check_rate_limit(_client_ip(request)):
        raise HTTPException(status_code=429, detail="rate limit exceeded")
    session = _authed_session(body.session_id, request)
    if body.thread_id is not None:
        body = body.model_copy(update={"thread_id": _resolve_thread(session.id, body.thread_id)})
    try:
        return await get_quiz_service().make_quiz(body)
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
    except QuotaExhaustedError:
        raise
    except (httpx.HTTPError, ConnectionError, TimeoutError, RuntimeError) as exc:
        raise _quiz_upstream_error(exc) from exc


@router.post("/quiz/note", response_model=MessagesResponse)
def post_quiz_note(body: QuizNoteRequest, request: Request) -> MessagesResponse:
    """Record a quiz in the chat it was asked from: started, or completed.

    The lines are written by the server from the structured fields
    (:func:`app.chat.quiz.quiz_note_lines`), never taken as text from the
    client. Returns the lines added, in the messages shape.
    """
    session = _authed_session(body.session_id, request)
    _use_thread(session.id, body.thread_id)
    added = [
        message_store.append(session.id, role, text, [])
        for role, text in quiz_note_lines(body)
    ]
    return MessagesResponse(
        session_id=session.id,
        messages=[
            MessageOut(role=m.role, content=m.content, citations=[], created_at=m.created_at)
            for m in added
        ],
        thread_id=current_thread.get(),
    )


@router.post("/quiz/grade", response_model=GradeResponse)
async def post_quiz_grade(body: GradeRequest, request: Request) -> GradeResponse:
    """Grade one subjective answer. MCQ and numerical are graded client-side."""
    if not _check_rate_limit(_client_ip(request)):
        raise HTTPException(status_code=429, detail="rate limit exceeded")
    # Grading reads only the question and answer: a signed-in learner is enough.
    if get_current_user(request) is None:
        raise HTTPException(status_code=401, detail="sign in required")
    try:
        return await get_quiz_service().grade(body)
    except QuotaExhaustedError:
        raise
    except (httpx.HTTPError, ConnectionError, TimeoutError, RuntimeError) as exc:
        raise _quiz_upstream_error(exc) from exc
