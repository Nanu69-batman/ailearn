"""Live voice: ``GET /voice/models``, ``WS /voice/live``, ``WS /voice/transcribe``.

Wire format: docs/VOICE_PROTOCOL.md. The Gemini key never leaves the server.
Course questions go through the same ``ChatService.chat`` path as ``POST /chat``
(via ``app.chat.router._answer``), so the strict timestamp gate, citation
validation and refusals are unchanged. Model text is never passed back as an
answer on its own; the tool response is the validated ``ChatResponse`` text.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket
from google.genai import types
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.websockets import WebSocketDisconnect

from app.auth.supabase import user_from_token
from app.chat import router as chat_router
from app.chat.launch import clamp_current_time_ms
from app.chat.prompts import strip_citation_markers
from app.chat.schemas import ChatMode, ChatRequest
from app.chat.sessions import session_store
from app.config import settings
from app.voice import gemini
from app.voice.models import VoiceModel, dialog_models, get_dialog_model, voice_enabled

log = logging.getLogger(__name__)

router = APIRouter()

START_TIMEOUT_S = 10.0
SESSION_LIMIT_S = 600.0
#: After ``stop`` in dictation, how long to wait for the last finals.
DRAIN_TIMEOUT_S = 3.0

UNAVAILABLE_MSG = "Voice is unavailable right now. Please try again."
COURSE_UNAVAILABLE_MSG = "The course assistant is unavailable right now."


@router.get("/voice/models")
def voice_models() -> dict:
    return {
        "enabled": voice_enabled(),
        "dialog": [
            {"key": m.key, "label": m.label, "model": m.model, "thinking": m.thinking}
            for m in dialog_models()
        ],
        "transcribe": {"model": settings.voice_transcribe_model},
    }


# --- shared plumbing ---------------------------------------------------------


class _StartBase(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: str = "start"
    session_id: str = Field(min_length=1, max_length=256)
    session_token: str | None = Field(default=None, max_length=512)
    access_token: str | None = Field(default=None, max_length=8192)


class LiveStart(_StartBase):
    model: str | None = Field(default=None, max_length=64)
    video_id: str | None = Field(default=None, max_length=256)
    current_time_ms: int = 0
    thread_id: str | None = Field(default=None, max_length=64)
    completed_resource_ids: list[str] = Field(default_factory=list, max_length=100)
    scope_resource_ids: list[str] | None = Field(default=None, max_length=256)
    general: bool = False
    mode: ChatMode = ChatMode.qa


class TranscribeStart(_StartBase):
    language: str | None = Field(default=None, max_length=16)


async def _send_json(ws: WebSocket, payload: dict) -> None:
    with contextlib.suppress(Exception):  # peer already gone
        await ws.send_json(payload)


async def _send_bytes(ws: WebSocket, data: bytes) -> None:
    with contextlib.suppress(Exception):  # peer already gone
        await ws.send_bytes(data)


async def _close(ws: WebSocket, code: int, reason: str = "") -> None:
    with contextlib.suppress(Exception):  # already closed
        await ws.close(code=code, reason=reason)


def _origin_ok(ws: WebSocket) -> bool:
    origin = ws.headers.get("origin")
    return origin is None or origin == settings.cors_origin


async def _read_start(ws: WebSocket) -> dict | None:
    """The first frame must be JSON ``{"type":"start"}`` within 10 s, else 4401."""
    try:
        msg = await asyncio.wait_for(ws.receive(), timeout=START_TIMEOUT_S)
    except (TimeoutError, WebSocketDisconnect, RuntimeError):
        await _close(ws, 4401, "start required")
        return None
    text = msg.get("text")
    try:
        data = json.loads(text) if text else None
    except ValueError:
        data = None
    if not isinstance(data, dict) or data.get("type") != "start":
        await _close(ws, 4401, "start required")
        return None
    return data


async def _authorize(ws: WebSocket, start: _StartBase, *, thread_id: str | None = None):
    """Same checks as ``POST /chat``. Returns the session or closes and returns None."""
    ip = ws.client.host if ws.client and ws.client.host else "unknown"
    if not chat_router._check_rate_limit(ip):
        await _close(ws, 4429, "rate limit exceeded")
        return None
    session = session_store.get(start.session_id)
    if session is None:
        await _close(ws, 4404, "session not found")
        return None
    try:
        user = user_from_token(start.access_token)
    except HTTPException:
        await _close(ws, 4401, "invalid token")
        return None
    owner = getattr(session, "owner_id", None)
    if owner is not None and (user is None or user.id != owner):
        await _close(ws, 4404, "session not found")
        return None
    try:
        chat_router._require_session_token(
            secret=settings.lms_launch_secret,
            session_id=session.id,
            learner_id=session.learner_id,
            token=start.session_token,
        )
    except HTTPException:
        await _close(ws, 4401, "invalid launch")
        return None
    if thread_id is not None:
        try:
            chat_router._use_thread(session.id, thread_id)
        except HTTPException:
            await _close(ws, 4404, "chat not found")
            return None
    return session


def _get(obj: Any, name: str) -> Any:
    return getattr(obj, name, None) if obj is not None else None


class _Stop(Exception):
    """Ends the receiver cleanly."""


async def _supervise(
    ws: WebSocket, tasks: list[asyncio.Task], *, timeout: float
) -> bool:
    """Wait for the first task to end (or the session cap), then cancel all.

    Returns False when the session hit its time limit (already closed 4408).
    """
    timed_out = False
    try:
        done, _ = await asyncio.wait(
            set(tasks), timeout=timeout, return_when=asyncio.FIRST_COMPLETED
        )
        if not done:
            timed_out = True
            await _send_json(ws, {"type": "closed", "reason": "time limit"})
            await _close(ws, 4408, "session time limit")
        else:
            failed = False
            for t in done:
                if not t.cancelled() and t.exception() is not None:
                    failed = True
                    log.warning("voice task failed: %s", type(t.exception()).__name__)
            if failed:
                await _send_json(ws, {"type": "error", "message": UNAVAILABLE_MSG})
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    return not timed_out


# --- conversation ------------------------------------------------------------


class _LiveSession:
    def __init__(self, ws: WebSocket, gs: Any, vm: VoiceModel, start: LiveStart):
        self.ws = ws
        self.gs = gs
        self.vm = vm
        self.start = start
        self.latest_ms = clamp_current_time_ms(start.current_time_ms)
        self.state = ""
        self.out_buf = ""
        self.tool_tasks: set[asyncio.Task] = set()
        self.reason = "ended"
        # Relay mode (thinking model): learner text awaiting its turn_complete.
        self.relay = vm.thinking
        self.pending = ""
        self.relay_busy = False
        self.relay_queued: str | None = None

    async def set_state(self, state: str) -> None:
        if state != self.state:
            self.state = state
            await _send_json(self.ws, {"type": "state", "state": state})

    # client -> Gemini
    async def read_client(self) -> None:
        while True:
            try:
                msg = await self.ws.receive()
            except (WebSocketDisconnect, RuntimeError):
                return
            if msg.get("type") == "websocket.disconnect":
                return
            data = msg.get("bytes")
            if data is not None:
                await self.gs.send_realtime_input(
                    audio=types.Blob(data=data, mime_type="audio/pcm;rate=16000")
                )
                continue
            try:
                body = json.loads(msg.get("text") or "")
            except ValueError:
                continue
            if not isinstance(body, dict):
                continue
            kind = body.get("type")
            if kind == "stop":
                self.reason = "stop"
                return
            if kind == "time":
                t = body.get("current_time_ms")
                if isinstance(t, (int, float)) and not isinstance(t, bool):
                    self.latest_ms = clamp_current_time_ms(int(t))
            elif kind == "text":
                text = body.get("text")
                if isinstance(text, str) and text.strip():
                    if self.relay:
                        self.pending = text.strip()[:4000]
                    await self.gs.send_client_content(
                        turns=types.Content(
                            role="user", parts=[types.Part(text=text.strip()[:4000])]
                        ),
                        turn_complete=True,
                    )

    # Gemini -> client
    async def receive_gemini(self) -> None:
        try:
            while True:
                got = False
                async for msg in self.gs.receive():
                    got = True
                    await self.on_message(msg)
                if not got:
                    await asyncio.sleep(0.05)
        except _Stop:
            return

    async def on_message(self, msg: Any) -> None:
        ws = self.ws
        sc = _get(msg, "server_content")
        if sc is not None:
            if _get(sc, "interrupted"):
                self.out_buf = ""
                await _send_json(ws, {"type": "interrupted"})
                await self.set_state("listening")
            turn = _get(sc, "model_turn")
            for part in _get(turn, "parts") or []:
                data = _get(_get(part, "inline_data"), "data")
                if data:
                    await self.set_state("speaking")
                    await _send_bytes(ws, data)
            interim = _get(_get(sc, "interim_input_transcription"), "text")
            if interim:
                await _send_json(
                    ws, {"type": "input_transcript", "text": interim, "final": False}
                )
            final = _get(_get(sc, "input_transcription"), "text")
            if final:
                if self.relay:
                    self.pending += final
                await _send_json(
                    ws, {"type": "input_transcript", "text": final, "final": True}
                )
            out = _get(_get(sc, "output_transcription"), "text")
            if out:
                self.out_buf += out
                await _send_json(
                    ws, {"type": "output_transcript", "text": self.out_buf}
                )
            status = _get(sc, "interaction_status")
            if status is not None:
                name = str(getattr(status, "value", status)).upper()
                if name.endswith("IN_PROGRESS"):
                    await self.set_state("thinking")
                elif name.endswith("IDLE"):
                    await self.set_state("listening")
            if _get(sc, "turn_complete"):
                self.out_buf = ""
                if self.relay and self.pending.strip():
                    question = self.pending.strip()[:4000]
                    self.pending = ""
                    self.relay_submit(question)
                # Thinking model: turn_complete alone does not mean idle.
                if not self.vm.thinking:
                    await self.set_state("listening")
        for call in _get(_get(msg, "tool_call"), "function_calls") or []:
            task = asyncio.create_task(self.run_tool(call))
            self.tool_tasks.add(task)
            task.add_done_callback(self.tool_tasks.discard)
        if _get(msg, "go_away") is not None:
            await _send_json(
                ws,
                {
                    "type": "error",
                    "message": "The voice session is ending. Reconnect to continue.",
                },
            )
            self.reason = "go_away"
            raise _Stop

    async def respond(self, call: Any, payload: dict) -> None:
        kwargs: dict[str, Any] = {}
        if self.vm.thinking:
            kwargs["scheduling"] = types.FunctionResponseScheduling.INTERRUPT
        fr = types.FunctionResponse(
            id=_get(call, "id"), name=_get(call, "name"), response=payload, **kwargs
        )
        await self.gs.send_tool_response(function_responses=[fr])

    async def ask_course(self, question: str) -> dict:
        """The shared pipeline: same path as POST /chat, never raises.

        Returns ``{"answer", "refused"[, "error"]}`` with citation markers
        stripped; sends the ``question`` and ``answer``/``error`` events.
        """
        await self.set_state("thinking")
        await _send_json(self.ws, {"type": "question", "text": question})
        s = self.start
        try:
            chat_router._use_thread(s.session_id, s.thread_id)
            body = ChatRequest(
                session_id=s.session_id,
                session_token=s.session_token,
                video_id=s.video_id,
                # The LATEST playhead, clamped; the strict gate lives in chat().
                current_time_ms=clamp_current_time_ms(self.latest_ms),
                message=question,
                mode=s.mode,
                thread_id=s.thread_id,
                completed_resource_ids=s.completed_resource_ids,
                scope_resource_ids=s.scope_resource_ids,
                general=s.general,
            )
            # On its own thread and loop: the pipeline has blocking stretches
            # (seconds) that would otherwise stall the live audio relay and
            # the Gemini socket's keepalive.
            resp = await asyncio.to_thread(asyncio.run, chat_router._answer(body))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - never leak provider text
            log.warning("voice ask_course failed: %s", type(exc).__name__)
            await _send_json(
                self.ws, {"type": "error", "message": COURSE_UNAVAILABLE_MSG}
            )
            return {"answer": "", "refused": True, "error": "unavailable"}
        await _send_json(
            self.ws, {"type": "answer", "response": resp.model_dump(mode="json")}
        )
        return {
            "answer": strip_citation_markers(resp.answer),
            "refused": bool(resp.refused),
        }

    async def run_tool(self, call: Any) -> None:
        args = _get(call, "args") or {}
        question = args.get("question") if isinstance(args, dict) else None
        question = question.strip()[:4000] if isinstance(question, str) else ""
        if _get(call, "name") != gemini.TOOL_NAME or not question:
            await self.respond(
                call, {"answer": "", "refused": True, "error": "bad_request"}
            )
            return
        await self.respond(call, await self.ask_course(question))

    # relay mode (thinking model)
    def relay_submit(self, question: str) -> None:
        if self.relay_busy:
            self.relay_queued = question  # only the latest waits
            return
        self.relay_busy = True
        task = asyncio.create_task(self.relay_loop(question))
        self.tool_tasks.add(task)
        task.add_done_callback(self.tool_tasks.discard)

    async def relay_loop(self, question: str | None) -> None:
        try:
            while question:
                payload = await self.ask_course(question)
                if payload.get("error"):
                    text = "COURSE ANSWER: UNAVAILABLE"
                elif payload["refused"]:
                    text = "COURSE ANSWER: REFUSED"
                else:
                    text = f"COURSE ANSWER: {payload['answer']}"
                await self.gs.send_client_content(
                    turns=types.Content(role="user", parts=[types.Part(text=text)]),
                    turn_complete=True,
                )
                question, self.relay_queued = self.relay_queued, None
        finally:
            self.relay_busy = False


@router.websocket("/voice/live")
async def voice_live(ws: WebSocket) -> None:
    await ws.accept()
    if not _origin_ok(ws):
        await _close(ws, 4403, "origin not allowed")
        return
    raw = await _read_start(ws)
    if raw is None:
        return
    try:
        start = LiveStart.model_validate(raw)
    except ValidationError:
        await _close(ws, 4400, "invalid start")
        return
    vm = get_dialog_model(start.model)
    if vm is None:
        await _close(ws, 4400, "unknown model")
        return
    if not voice_enabled():
        await _send_json(ws, {"type": "error", "message": UNAVAILABLE_MSG})
        await _close(ws, 4503, "voice disabled")
        return
    if await _authorize(ws, start, thread_id=start.thread_id) is None:
        return

    try:
        cm = gemini.connect(vm.model, gemini.live_config(vm))
        gs = await cm.__aenter__()
    except Exception as exc:  # noqa: BLE001 - never leak provider text
        log.warning("voice connect failed: %s", type(exc).__name__)
        await _send_json(ws, {"type": "error", "message": UNAVAILABLE_MSG})
        await _close(ws, 1011, "unavailable")
        return
    live = _LiveSession(ws, gs, vm, start)
    in_time = True
    try:
        await _send_json(ws, {"type": "ready", "model": vm.key, "label": vm.label})
        await live.set_state("listening")
        tasks = [
            asyncio.create_task(live.read_client()),
            asyncio.create_task(live.receive_gemini()),
        ]
        in_time = await _supervise(ws, tasks, timeout=SESSION_LIMIT_S)
    finally:
        for t in list(live.tool_tasks):
            t.cancel()
        await asyncio.gather(*live.tool_tasks, return_exceptions=True)
        with contextlib.suppress(Exception):
            await cm.__aexit__(None, None, None)
    if in_time:
        await _send_json(ws, {"type": "closed", "reason": live.reason})
        await _close(ws, 1000)


# --- dictation ---------------------------------------------------------------


@router.websocket("/voice/transcribe")
async def voice_transcribe(ws: WebSocket) -> None:
    await ws.accept()
    if not _origin_ok(ws):
        await _close(ws, 4403, "origin not allowed")
        return
    raw = await _read_start(ws)
    if raw is None:
        return
    try:
        start = TranscribeStart.model_validate(raw)
    except ValidationError:
        await _close(ws, 4400, "invalid start")
        return
    if not voice_enabled():
        await _send_json(ws, {"type": "error", "message": UNAVAILABLE_MSG})
        await _close(ws, 4503, "voice disabled")
        return
    if await _authorize(ws, start) is None:
        return

    try:
        cm = gemini.connect(
            settings.voice_transcribe_model, gemini.transcribe_config(start.language)
        )
        gs = await cm.__aenter__()
    except Exception as exc:  # noqa: BLE001 - never leak provider text
        log.warning("transcribe connect failed: %s", type(exc).__name__)
        await _send_json(ws, {"type": "error", "message": UNAVAILABLE_MSG})
        await _close(ws, 1011, "unavailable")
        return

    stopped = asyncio.Event()

    async def read_client() -> None:
        while True:
            try:
                msg = await ws.receive()
            except (WebSocketDisconnect, RuntimeError):
                return
            if msg.get("type") == "websocket.disconnect":
                return
            data = msg.get("bytes")
            if data is not None:
                if stopped.is_set():
                    continue
                await gs.send_realtime_input(
                    audio=types.Blob(data=data, mime_type="audio/pcm;rate=16000")
                )
                continue
            try:
                body = json.loads(msg.get("text") or "")
            except ValueError:
                continue
            is_stop = isinstance(body, dict) and body.get("type") == "stop"
            if is_stop and not stopped.is_set():
                await gs.send_realtime_input(audio_stream_end=True)
                stopped.set()

    async def receive_gemini() -> None:
        while True:
            got = False
            async for msg in gs.receive():
                got = True
                sc = _get(msg, "server_content")
                interim = _get(_get(sc, "interim_input_transcription"), "text")
                if interim:
                    await _send_json(ws, {"type": "interim", "text": interim})
                final = _get(_get(sc, "input_transcription"), "text")
                if final:
                    await _send_json(ws, {"type": "final", "text": final})
                if _get(sc, "turn_complete") and stopped.is_set():
                    return
            if not got:
                await asyncio.sleep(0.05)

    async def drain_deadline() -> None:
        await stopped.wait()
        await asyncio.sleep(DRAIN_TIMEOUT_S)

    in_time = True
    try:
        await _send_json(ws, {"type": "ready"})
        tasks = [
            asyncio.create_task(read_client()),
            asyncio.create_task(receive_gemini()),
            asyncio.create_task(drain_deadline()),
        ]
        in_time = await _supervise(ws, tasks, timeout=SESSION_LIMIT_S)
    finally:
        with contextlib.suppress(Exception):
            await cm.__aexit__(None, None, None)
    if in_time:
        await _send_json(ws, {"type": "closed"})
        await _close(ws, 1000)
