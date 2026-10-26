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
