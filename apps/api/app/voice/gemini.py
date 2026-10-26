"""The one place that talks to Gemini Live. Tests monkeypatch ``connect``."""

from __future__ import annotations

from typing import Any

from google.genai import types

from app.chat.prompts import personalization_note
from app.config import settings
from app.voice.models import VoiceModel, tone_instruction, voice_name

TOOL_NAME = "ask_course"

SYSTEM_INSTRUCTION = (
    "You are Malai, a spoken study tutor. You NEVER answer questions about the "
    "course material from your own knowledge. For every question about the "
    "course, lesson, video, slides or documents, call the ask_course tool with "
    "the learner's question (rewritten to stand alone if needed). Then speak the "
    "returned answer faithfully and naturally. Add no facts that are not in it. "
    "If the tool result says refused is true, tell the learner plainly that the "
    "course material so far does not cover that; do not guess. If the tool "
    "returns an error, say you cannot reach the course right now. Greetings and "
    "checks such as 'can you hear me' may be answered without the tool. Call "
    "the tool straight away: before it, say nothing or at most a few words, and "
    "never repeat a filler. Keep replies short and conversational."
)


RELAY_SYSTEM_INSTRUCTION = (
    "You are Malai, a spoken study tutor. You NEVER answer course questions "
    "from your own knowledge. When the learner asks something, say only a very "
    "short acknowledgement such as 'Let me check the course' and stop. The "
    "system will then send a message starting with COURSE ANSWER. Speak that "
    "answer faithfully and naturally, adding no facts. If it says REFUSED, tell "
    "the learner the course material so far does not cover that; if it says "
    "UNAVAILABLE, say you cannot reach the course right now. Keep replies short."
)

#: How to speak. The course answers already follow the chat's language rule;
#: this keeps the voice itself in the same register.
SPOKEN_LANGUAGE_RULE = (
    "Speak the learner's language. If they speak Hindi or Hinglish, speak "
    "natural Hinglish the way English-medium students in India talk: Hindi "
    "sentence flow with English technical terms and most content words in "
    "English. Never use formal or shuddh Hindi words; say 'example', "
    "'important', 'concept'. Explain concepts in that same Hinglish."
)


def connect(model: str, config: types.LiveConnectConfig) -> Any:
    """Async context manager yielding a Gemini Live session."""
    from google import genai

    client = genai.Client(api_key=settings.llm_api_key)
    return client.aio.live.connect(model=model, config=config)


def ask_course_tool(*, non_blocking: bool) -> types.Tool:
    decl = types.FunctionDeclaration(
        name=TOOL_NAME,
        description=(
            "Answer a question about the learner's course material, gated to "
            "what they have reached so far. Returns the answer text."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "question": types.Schema(
                    type=types.Type.STRING,
                    description="The learner's question about the course.",
                )
            },
            required=["question"],
        ),
        behavior=types.Behavior.NON_BLOCKING if non_blocking else types.Behavior.BLOCKING,
    )
    return types.Tool(function_declarations=[decl])


def system_instruction(
    vm: VoiceModel, *, tone: str | None = None, personalization: Any = None
) -> str:
    base = RELAY_SYSTEM_INSTRUCTION if vm.thinking else SYSTEM_INSTRUCTION
    parts = [base, SPOKEN_LANGUAGE_RULE, tone_instruction(tone)]
    note = personalization_note(personalization)
    if note:
        parts.append(note)
    return "\n\n".join(parts)


def live_config(
    vm: VoiceModel,
    *,
    voice: str | None = None,
    tone: str | None = None,
    personalization: Any = None,
) -> types.LiveConnectConfig:
    kwargs: dict[str, Any] = {}
    if vm.thinking:
        kwargs["thinking_config"] = types.ThinkingConfig(
            thinking_level=settings.voice_thinking_level
        )
    # The extended-thinking model never emits tool calls to the client (seen
    # live, 2026-10-26), so it runs in server-driven relay mode: no tools, the
    # server asks the course pipeline itself and injects the answer as a turn.
    kwargs["system_instruction"] = system_instruction(
        vm, tone=tone, personalization=personalization
    )
    if not vm.thinking:
        kwargs["tools"] = [ask_course_tool(non_blocking=False)]
    return types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                    voice_name=voice_name(voice)
                )
            )
        ),
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        **kwargs,
    )


def transcribe_config(language: str | None) -> types.LiveConnectConfig:
    return types.LiveConnectConfig(
        response_modalities=["TEXT"],
        input_audio_transcription=types.AudioTranscriptionConfig(
            language_codes=[language] if language else [], mode="SMART"
        ),
    )
