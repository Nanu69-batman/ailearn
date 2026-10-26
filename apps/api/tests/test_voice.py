"""Live voice endpoints (docs/VOICE_PROTOCOL.md). The Gemini session is faked."""

from __future__ import annotations

import asyncio
import contextlib
import time
from types import SimpleNamespace as NS

import jwt
import pytest
from fastapi.testclient import TestClient
from google.genai import types
from starlette.websockets import WebSocketDisconnect

from app.chat import router as chat_router
from app.chat.launch import MAX_CURRENT_TIME_MS
from app.chat.schemas import ChatResponse
from app.config import settings
from app.main import app
from app.voice import gemini

JWT_SECRET = "test-jwt-secret-at-least-32-bytes-long!!"


class FakeSession:
    """Stands in for a google-genai live session. ``script`` is queued on first audio."""

    def __init__(self, script=None, after_end=None):
        self.script = list(script or [])
        self.after_end = list(after_end or [])
        self.q: asyncio.Queue = asyncio.Queue()
        self.audio: list[bytes] = []
        self.stream_end = False
        self.client_content: list = []
        self.tool_responses: list = []
        self._scripted = False

    async def send_realtime_input(self, audio=None, audio_stream_end=None, **_):
        if audio is not None:
            assert audio.mime_type == "audio/pcm;rate=16000"
            self.audio.append(audio.data)
            if not self._scripted:
                self._scripted = True
                for m in self.script:
                    self.q.put_nowait(m)
        if audio_stream_end:
            self.stream_end = True
            for m in self.after_end:
                self.q.put_nowait(m)

    async def send_client_content(self, turns=None, turn_complete=None):
        self.client_content.append((turns, turn_complete))

    async def send_tool_response(self, function_responses):
        self.tool_responses.extend(function_responses)

    async def receive(self):
        while True:
            yield await self.q.get()


class FakeConnect:
    def __init__(self, session: FakeSession):
        self.session = session
        self.calls: list[tuple[str, types.LiveConnectConfig]] = []

    def __call__(self, model, config):
        self.calls.append((model, config))
        session = self.session

        class _CM:
            async def __aenter__(self_):
                return session

            async def __aexit__(self_, *a):
                return False

        return _CM()


class FakeService:
    def __init__(self, answer="Gradient descent steps downhill. [chunk_id=c1]", exc=None):
        self.calls: list[dict] = []
        self.answer = answer
        self.exc = exc

    async def chat(self, **kw):
        self.calls.append(kw)
        if self.exc:
            raise self.exc
        return ChatResponse(answer=self.answer, refused=False)


def _tool_call(question="what is gradient descent?", name="ask_course", cid="call-1"):
    return NS(
        server_content=None,
        tool_call=NS(function_calls=[NS(id=cid, name=name, args={"question": question})]),
        go_away=None,
    )


def _audio_out(data=b"\x01\x02\x03\x04"):
    part = NS(inline_data=NS(data=data))
    return NS(
        server_content=NS(model_turn=NS(parts=[part])), tool_call=None, go_away=None
    )


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "supabase_url", "")
    monkeypatch.setattr(settings, "supabase_service_role_key", "")
    monkeypatch.setattr(settings, "supabase_jwt_secret", "")
    monkeypatch.setattr(settings, "auth_required", False)
    monkeypatch.setattr(settings, "allow_unsigned_launch", True)
    monkeypatch.setattr(settings, "llm_api_key", "test-key")
    chat_router.reset_chat_state()
    yield TestClient(app)
    chat_router.reset_chat_state()


@pytest.fixture
def launch(client):
    r = client.post(
        "/lms/session",
        json={
            "learner_id": "learner-1",
            "course_id": "c1",
            "resource_id": "r1",
            "resource_scope": ["r1"],
            "current_time_ms": 0,
            "issued_at": int(time.time() * 1000),
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def _install(monkeypatch, session: FakeSession) -> FakeConnect:
    fc = FakeConnect(session)
    monkeypatch.setattr(gemini, "connect", fc)
    return fc


def _start(launch, **extra):
    return {
        "type": "start",
        "session_id": launch["session_id"],
        "session_token": launch["session_token"],
        "current_time_ms": 1000,
        **extra,
    }


def _read_until(ws, pred, limit=50):
    """Collect frames (json dicts, or bytes) until ``pred(frame)`` is true."""
    seen = []
    for _ in range(limit):
        msg = ws.receive()
        if msg["type"] == "websocket.close":
            raise WebSocketDisconnect(msg.get("code", 1000))
        if msg.get("bytes") is not None:
            frame = msg["bytes"]
        else:
            import json

            frame = json.loads(msg["text"])
        seen.append(frame)
        if pred(frame):
            return seen
    raise AssertionError(f"predicate never matched: {seen}")


def _is(kind):
    return lambda f: isinstance(f, dict) and f.get("type") == kind



@contextlib.contextmanager
def _open_live(client):
    """Open /voice/live and end it with ``stop`` so the server finishes cleanly."""
    with client.websocket_connect("/voice/live") as ws:
        yield ws
        ws.send_json({"type": "stop"})
        with contextlib.suppress(WebSocketDisconnect, AssertionError):
            _read_until(ws, _is("closed"), limit=200)


def _wait(cond, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


# --- GET /voice/models -------------------------------------------------------


def test_models_enabled_false_without_key(client, monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "")
    body = client.get("/voice/models").json()
    assert body["enabled"] is False
    assert [m["key"] for m in body["dialog"]] == ["live", "live-thinking", "native-audio"]


def test_models_enabled_true_with_key(client):
    body = client.get("/voice/models").json()
    assert body["enabled"] is True
    live, thinking, native = body["dialog"]
    assert live == {
        "key": "live",
        "label": "Gemini 3.8 Live",
        "model": "gemini-3.8-live",
        "thinking": False,
    }
    assert thinking["label"] == "Gemini 3.8 Live · Extended thinking"
    assert thinking["thinking"] is True
    assert native["label"] == "Gemini 2.5 Flash Native Audio Dialog"
    assert native["model"] == "gemini-2.5-flash-native-audio-preview-12-2025"
    assert body["transcribe"] == {"model": "gemini-3.5-transcribe-live"}


# --- start auth --------------------------------------------------------------


@pytest.mark.parametrize("path", ["/voice/live", "/voice/transcribe"])
def test_bad_session_token_is_4401(client, launch, monkeypatch, path):
    _install(monkeypatch, FakeSession())
    with client.websocket_connect(path) as ws:
        ws.send_json(_start(launch, session_token="forged"))
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_json()
    assert e.value.code == 4401


@pytest.mark.parametrize("path", ["/voice/live", "/voice/transcribe"])
def test_missing_session_is_4404(client, launch, monkeypatch, path):
    _install(monkeypatch, FakeSession())
    with client.websocket_connect(path) as ws:
        ws.send_json(_start(launch, session_id="sess_nope"))
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_json()
    assert e.value.code == 4404


def test_bad_access_token_is_4401(client, launch, monkeypatch):
    monkeypatch.setattr(settings, "supabase_jwt_secret", JWT_SECRET)
    _install(monkeypatch, FakeSession())
    with client.websocket_connect("/voice/live") as ws:
        ws.send_json(_start(launch, access_token="garbage"))
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_json()
    assert e.value.code == 4401


def test_other_users_session_is_4404(client, monkeypatch):
    monkeypatch.setattr(settings, "supabase_jwt_secret", JWT_SECRET)
    now = int(time.time())

    def tok(sub):
        return jwt.encode(
            {"sub": sub, "aud": "authenticated", "exp": now + 3600},
            JWT_SECRET,
            algorithm="HS256",
        )

    r = client.post(
        "/lms/session",
        headers={"Authorization": f"Bearer {tok('owner')}"},
        json={
            "learner_id": "x",
            "course_id": "c1",
            "resource_id": "r1",
            "resource_scope": ["r1"],
            "current_time_ms": 0,
            "issued_at": now * 1000,
        },
    ).json()
    _install(monkeypatch, FakeSession())
    with client.websocket_connect("/voice/live") as ws:
        ws.send_json(
            {
                "type": "start",
                "session_id": r["session_id"],
                "session_token": r["session_token"],
                "access_token": tok("someone-else"),
                "current_time_ms": 0,
            }
        )
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_json()
    assert e.value.code == 4404


def test_first_frame_must_be_start(client):
    with client.websocket_connect("/voice/live") as ws:
        ws.send_json({"type": "time", "current_time_ms": 1})
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_json()
    assert e.value.code == 4401


def test_foreign_origin_is_4403(client, launch, monkeypatch):
    _install(monkeypatch, FakeSession())
    with client.websocket_connect(
        "/voice/live", headers={"origin": "http://evil.example"}
    ) as ws, pytest.raises(WebSocketDisconnect) as e:
        ws.receive_json()
    assert e.value.code == 4403


# --- conversation ------------------------------------------------------------


def test_tool_call_runs_chat_with_latest_clamped_time(client, launch, monkeypatch):
    svc = FakeService()
    chat_router.set_service_override(svc)
    fake = FakeSession(script=[_tool_call()])
    _install(monkeypatch, fake)

    with client.websocket_connect("/voice/live") as ws:
        ws.send_json(_start(launch, video_id="r1", completed_resource_ids=["r2"]))
        ready = ws.receive_json()
        assert ready == {"type": "ready", "model": "live", "label": "Gemini 3.8 Live"}
        ws.send_json({"type": "time", "current_time_ms": 5000})
        ws.send_json({"type": "time", "current_time_ms": MAX_CURRENT_TIME_MS * 3})
        ws.send_json({"type": "time", "current_time_ms": 42_000})
        ws.send_bytes(b"\x00\x01" * 8)  # first audio releases the scripted tool call
        frames = _read_until(ws, _is("answer"))
        assert _wait(lambda: fake.tool_responses)
        ws.send_json({"type": "stop"})
        _read_until(ws, _is("closed"))

    types_seen = [f["type"] for f in frames if isinstance(f, dict)]
    assert "question" in types_seen
    question = next(f for f in frames if isinstance(f, dict) and f["type"] == "question")
    assert question["text"] == "what is gradient descent?"
    answer = frames[-1]
    assert answer["response"]["answer"].startswith("Gradient descent")
    assert answer["response"]["refused"] is False

    assert len(svc.calls) == 1
    call = svc.calls[0]
    assert call["current_time_ms"] == 42_000  # the LATEST playhead
    assert call["message"] == "what is gradient descent?"
    assert call["session_id"] == launch["session_id"]
    assert call["focus_resource_id"] == "r1"
    assert call["completed_resource_ids"] == ["r2"]
    assert call["mode"] == "qa"

    fr = fake.tool_responses[0]
    assert fr.id == "call-1" and fr.name == "ask_course"
    assert fr.response["refused"] is False
    assert fr.response["answer"].startswith("Gradient descent steps downhill.")
    assert "chunk_id" not in fr.response["answer"]


def test_playhead_is_clamped_to_server_ceiling(client, launch, monkeypatch):
    svc = FakeService()
    chat_router.set_service_override(svc)
    fake = FakeSession(script=[_tool_call()])
    _install(monkeypatch, fake)
    with _open_live(client) as ws:
        ws.send_json(_start(launch))
        ws.receive_json()
        ws.send_json({"type": "time", "current_time_ms": MAX_CURRENT_TIME_MS * 3})
        ws.send_bytes(b"\x00\x00")
        _read_until(ws, _is("answer"))
    assert svc.calls[0]["current_time_ms"] == MAX_CURRENT_TIME_MS


def test_chat_failure_sends_unavailable_and_no_exception_text(client, launch, monkeypatch):
    chat_router.set_service_override(FakeService(exc=RuntimeError("boom-secret-detail")))
    fake = FakeSession(script=[_tool_call()])
    _install(monkeypatch, fake)
    with _open_live(client) as ws:
        ws.send_json(_start(launch))
        ws.receive_json()
        ws.send_bytes(b"\x00\x00")
        frames = _read_until(ws, _is("error"))
        assert _wait(lambda: fake.tool_responses)
    assert "boom-secret-detail" not in repr(frames)
    assert fake.tool_responses[0].response == {
        "answer": "",
        "refused": True,
        "error": "unavailable",
    }


def test_audio_relayed_both_ways_as_binary(client, launch, monkeypatch):
    out = b"\x10\x20\x30\x40"
    fake = FakeSession(script=[_audio_out(out)])
    _install(monkeypatch, fake)
    chunk = b"\x07\x08" * 1600
    with _open_live(client) as ws:
        ws.send_json(_start(launch))
        ws.receive_json()
        ws.send_bytes(chunk)
        frames = _read_until(ws, lambda f: isinstance(f, bytes))
        assert frames[-1] == out
        assert {"type": "state", "state": "speaking"} in [
            f for f in frames if isinstance(f, dict)
        ]
    assert fake.audio == [chunk]


def test_transcripts_and_interrupt_events(client, launch, monkeypatch):
    sc = NS(
        interim_input_transcription=NS(text="what is"),
        input_transcription=NS(text="what is a tensor"),
        output_transcription=NS(text="A tensor "),
        interrupted=True,
        turn_complete=False,
        model_turn=None,
        interaction_status=None,
    )
    fake = FakeSession(script=[NS(server_content=sc, tool_call=None, go_away=None)])
    _install(monkeypatch, fake)
    with _open_live(client) as ws:
        ws.send_json(_start(launch))
        ws.receive_json()
        ws.send_bytes(b"\x00\x00")
        frames = _read_until(ws, _is("output_transcript"))
    assert {"type": "interrupted"} in frames
    assert {"type": "input_transcript", "text": "what is", "final": False} in frames
    assert {"type": "input_transcript", "text": "what is a tensor", "final": True} in frames
    assert {"type": "output_transcript", "text": "A tensor "} in frames


def test_text_turn_is_forwarded(client, launch, monkeypatch):
    fake = FakeSession()
    _install(monkeypatch, fake)
    with _open_live(client) as ws:
        ws.send_json(_start(launch))
        ws.receive_json()
        ws.send_json({"type": "text", "text": "hello there"})
        assert _wait(lambda: fake.client_content)
    turns, complete = fake.client_content[0]
    assert complete is True
    assert turns.parts[0].text == "hello there"


@pytest.mark.parametrize(
    "key,model_attr",
    [("live", "voice_live_model"), ("native-audio", "voice_native_audio_model")],
)
def test_tool_models_use_blocking_and_no_thinking(client, launch, monkeypatch, key, model_attr):
    chat_router.set_service_override(FakeService())
    fake = FakeSession(script=[_tool_call()])
    fc = _install(monkeypatch, fake)
    with _open_live(client) as ws:
        ws.send_json(_start(launch, model=key))
        assert ws.receive_json()["model"] == key
        ws.send_bytes(b"\x00\x00")
        _read_until(ws, _is("answer"))
        assert _wait(lambda: fake.tool_responses)

    model, config = fc.calls[0]
    assert model == getattr(settings, model_attr)
    decl = config.tools[0].function_declarations[0]
    assert decl.name == "ask_course"
    assert decl.behavior == types.Behavior.BLOCKING
    assert config.thinking_config is None
    assert fake.tool_responses[0].scheduling is None


def _relay_session(client, launch, monkeypatch, svc, script_extra=()):
    chat_router.set_service_override(svc)
    transcript = NS(
        server_content=NS(input_transcription=NS(text="what is a tensor")),
        tool_call=None,
        go_away=None,
    )
    done = NS(server_content=NS(turn_complete=True), tool_call=None, go_away=None)
    fake = FakeSession(script=[transcript, done, *script_extra])
    fc = _install(monkeypatch, fake)
    return fake, fc


def test_thinking_model_declares_no_tools_and_relays_course_answer(client, launch, monkeypatch):
    svc = FakeService(answer="A tensor is an array. [chunk_id=c1]")
    fake, fc = _relay_session(client, launch, monkeypatch, svc)
    with _open_live(client) as ws:
        ws.send_json(_start(launch, model="live-thinking"))
        ws.receive_json()
        ws.send_json({"type": "time", "current_time_ms": 7000})
        ws.send_json({"type": "time", "current_time_ms": 9000})
        ws.send_bytes(b"\x00\x00")
        frames = _read_until(ws, _is("answer"))
        assert _wait(lambda: fake.client_content)

    _, config = fc.calls[0]
    assert config.tools is None
    assert config.thinking_config.thinking_level == types.ThinkingLevel.MEDIUM
    assert "COURSE ANSWER" in config.system_instruction
    assert {"type": "question", "text": "what is a tensor"} in frames
    assert len(svc.calls) == 1
    assert svc.calls[0]["current_time_ms"] == 9000
    assert svc.calls[0]["message"] == "what is a tensor"
    turns, complete = fake.client_content[0]
    assert complete is True
    text = turns.parts[0].text
    assert text.startswith("COURSE ANSWER: A tensor is an array.")
    assert "chunk_id" not in text
    # The injected turn is not echoed back as the learner's speech.
    assert not [
        f for f in frames if isinstance(f, dict) and f.get("text", "").startswith("COURSE")
    ]


def test_relay_refusal_is_injected_as_refused(client, launch, monkeypatch):
    class Refusing(FakeService):
        async def chat(self, **kw):
            self.calls.append(kw)
            return ChatResponse(answer="Not covered yet.", refused=True)

    fake, _ = _relay_session(client, launch, monkeypatch, Refusing())
    with _open_live(client) as ws:
        ws.send_json(_start(launch, model="live-thinking"))
        ws.receive_json()
        ws.send_bytes(b"\x00\x00")
        _read_until(ws, _is("answer"))
        assert _wait(lambda: fake.client_content)
    assert fake.client_content[0][0].parts[0].text == "COURSE ANSWER: REFUSED"


def test_relay_failure_is_injected_as_unavailable_without_detail(client, launch, monkeypatch):
    fake, _ = _relay_session(
        client, launch, monkeypatch, FakeService(exc=RuntimeError("boom-secret-detail"))
    )
    with _open_live(client) as ws:
        ws.send_json(_start(launch, model="live-thinking"))
        ws.receive_json()
        ws.send_bytes(b"\x00\x00")
        frames = _read_until(ws, _is("error"))
        assert _wait(lambda: fake.client_content)
    assert "boom-secret-detail" not in repr(frames)
    assert fake.client_content[0][0].parts[0].text == "COURSE ANSWER: UNAVAILABLE"


def test_thinking_model_state_follows_interaction_status(client, launch, monkeypatch):
    def status(s, turn_complete=False):
        sc = NS(interaction_status=NS(value=s), turn_complete=turn_complete)
        return NS(server_content=sc, tool_call=None, go_away=None)

    fake = FakeSession(
        script=[status("IN_PROGRESS"), status("IN_PROGRESS", turn_complete=True), status("IDLE")]
    )
    _install(monkeypatch, fake)
    with _open_live(client) as ws:
        ws.send_json(_start(launch, model="live-thinking"))
        ws.receive_json()
        ws.send_bytes(b"\x00\x00")
        frames = _read_until(ws, lambda f: f == {"type": "state", "state": "thinking"})
        # turn_complete alone must not flip to listening; only IDLE does.
        frames = _read_until(ws, lambda f: f == {"type": "state", "state": "listening"})
    assert {"type": "state", "state": "listening"} not in frames[:-1]


def test_unknown_model_is_4400(client, launch, monkeypatch):
    _install(monkeypatch, FakeSession())
    with client.websocket_connect("/voice/live") as ws:
        ws.send_json(_start(launch, model="nope"))
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_json()
    assert e.value.code == 4400


def test_connect_failure_sends_safe_error(client, launch, monkeypatch):
    def boom(model, config):
        raise RuntimeError("provider-internal-detail test-key")

    monkeypatch.setattr(gemini, "connect", boom)
    with client.websocket_connect("/voice/live") as ws:
        ws.send_json(_start(launch))
        frame = ws.receive_json()
        assert frame["type"] == "error"
        assert "provider-internal" not in frame["message"]
        assert "test-key" not in frame["message"]


# --- dictation ---------------------------------------------------------------


def test_transcribe_relays_interim_final_and_ends_stream_on_stop(client, launch, monkeypatch):
    def msg(**sc):
        return NS(server_content=NS(**sc), tool_call=None, go_away=None)

    fake = FakeSession(
        script=[
            msg(interim_input_transcription=NS(text="hel")),
            msg(input_transcription=NS(text="hello ")),
        ],
        after_end=[
            msg(input_transcription=NS(text="world")),
            msg(turn_complete=True),
        ],
    )
    fc = _install(monkeypatch, fake)
    with client.websocket_connect("/voice/transcribe") as ws:
        ws.send_json(
            {
                "type": "start",
                "session_id": launch["session_id"],
                "session_token": launch["session_token"],
                "language": "en",
            }
        )
        assert ws.receive_json() == {"type": "ready"}
        ws.send_bytes(b"\x01\x02" * 100)
        frames = _read_until(ws, lambda f: f == {"type": "final", "text": "hello "})
        ws.send_json({"type": "stop"})
        rest = _read_until(ws, _is("closed"))

    all_frames = frames + rest
    assert {"type": "interim", "text": "hel"} in all_frames
    assert {"type": "final", "text": "world"} in rest
    assert fake.stream_end is True
    model, config = fc.calls[0]
    assert model == settings.voice_transcribe_model
    assert [m.value for m in config.response_modalities] == ["TEXT"]
    assert config.input_audio_transcription.mode == types.AudioTranscriptionConfigMode.SMART
    assert config.input_audio_transcription.language_codes == ["en"]


# --- voices, language, recap -------------------------------------------------


def test_models_list_voices_and_tones(client):
    body = client.get("/voice/models").json()
    names = [v["name"] for v in body["voices"]]
    assert body["default_voice"] in names and "Kore" in names
    assert body["default_tone"] in [t["key"] for t in body["tones"]]


def test_live_config_carries_voice_tone_language_and_personalization():
    from app.chat.schemas import Personalization
    from app.voice.models import get_dialog_model

    vm = get_dialog_model("live")
    cfg = gemini.live_config(
        vm, voice="Kore", tone="calm", personalization=Personalization(name="Rohan")
    )
    assert cfg.speech_config.voice_config.prebuilt_voice_config.voice_name == "Kore"
    assert "Hinglish" in cfg.system_instruction
    assert "unhurried" in cfg.system_instruction
    assert "Rohan" in cfg.system_instruction
    unknown = gemini.live_config(vm, voice="NotAVoice")
    assert unknown.speech_config.voice_config.prebuilt_voice_config.voice_name == "Puck"


def test_transliterate_devanagari_to_hinglish():
    from app.voice.hinglish import has_devanagari, transliterate

    out = transliterate("मुझे समझ नहीं आ रहा है")
    assert not has_devanagari(out)
    assert out == "mujhe samajh nahin aa raha hai"
    assert transliterate("plain English") == "plain English"


def test_voice_style_note_only_for_voice():
    from app.chat.prompts import VOICE_STYLE, style_for

    assert style_for(None) == ""
    assert VOICE_STYLE in style_for(None, voice=True)


def test_stop_sends_recap_saved_to_chat_and_uses_fast_pipeline(client, launch, monkeypatch):
    from app.chat.sessions import MAIN_THREAD, message_store

    svc = FakeService()
    chat_router.set_service_override(svc)
    fake = FakeSession(script=[_tool_call()])
    _install(monkeypatch, fake)
    with client.websocket_connect("/voice/live") as ws:
        ws.send_json(_start(launch))
        ws.receive_json()
        ws.send_bytes(b"\x00\x00")
        _read_until(ws, _is("answer"))
        assert _wait(lambda: fake.tool_responses)
        ws.send_json({"type": "stop"})
        frames = _read_until(ws, _is("closed"), limit=200)
    recap = next(f for f in frames if isinstance(f, dict) and f.get("type") == "recap")
    assert "Voice chat recap" in recap["response"]["answer"]
    assert "what is gradient descent?" in recap["response"]["answer"]
    assert svc.calls[0]["fast"] is True
    saved = message_store.list_for_session(launch["session_id"], MAIN_THREAD)
    assert [m.role for m in saved] == ["assistant"]
    assert "Voice chat recap" in saved[0].content


def test_no_recap_without_exchanges(client, launch, monkeypatch):
    _install(monkeypatch, FakeSession())
    with client.websocket_connect("/voice/live") as ws:
        ws.send_json(_start(launch))
        ws.receive_json()
        ws.send_json({"type": "stop"})
        frames = _read_until(ws, _is("closed"), limit=200)
    assert not any(isinstance(f, dict) and f.get("type") == "recap" for f in frames)
