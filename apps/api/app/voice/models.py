"""Voice model registry (docs/VOICE_PROTOCOL.md, "Models")."""

from __future__ import annotations

from dataclasses import dataclass

from app.config import settings


@dataclass(frozen=True)
class VoiceModel:
    key: str
    label: str
    model: str
    #: Extended-thinking model: ThinkingConfig + NON_BLOCKING tools.
    thinking: bool = False


DEFAULT_KEY = "live"


def dialog_models() -> list[VoiceModel]:
    """Read at call time so a settings change (or a test) takes effect."""
    return [
        VoiceModel("live", "Gemini 3.8 Live", settings.voice_live_model),
        VoiceModel(
            "live-thinking",
            "Gemini 3.8 Live · Extended thinking",
            settings.voice_thinking_model,
            thinking=True,
        ),
        VoiceModel(
            "native-audio",
            "Gemini 2.5 Flash Native Audio Dialog",
            settings.voice_native_audio_model,
        ),
    ]


def get_dialog_model(key: str | None) -> VoiceModel | None:
    wanted = key or DEFAULT_KEY
    return next((m for m in dialog_models() if m.key == wanted), None)


def voice_enabled() -> bool:
    return bool(settings.llm_api_key)


#: Gemini Live prebuilt voices offered in the picker: (name, description).
VOICES: list[tuple[str, str]] = [
    ("Puck", "Upbeat"),
    ("Kore", "Firm"),
    ("Aoede", "Breezy"),
    ("Charon", "Informative"),
    ("Leda", "Youthful"),
    ("Zephyr", "Bright"),
    ("Orus", "Steady"),
    ("Fenrir", "Excitable"),
]
DEFAULT_VOICE = "Puck"

#: Speaking styles: key -> (label, instruction for the voice model).
TONES: dict[str, tuple[str, str]] = {
    "friendly": ("Friendly", "Sound warm, friendly and encouraging."),
    "calm": ("Calm", "Speak calmly and patiently, at an unhurried pace."),
    "energetic": ("Energetic", "Sound lively and enthusiastic, with upbeat energy."),
    "professional": ("Professional", "Sound clear, precise and professional."),
}
DEFAULT_TONE = "friendly"


def voice_name(name: str | None) -> str:
    names = {n for n, _ in VOICES}
    return name if name in names else DEFAULT_VOICE


def tone_instruction(key: str | None) -> str:
    return TONES.get(key or DEFAULT_TONE, TONES[DEFAULT_TONE])[1]
