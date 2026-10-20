"""Hosted-only switches. Fakes only."""

from __future__ import annotations

import pytest

from app.config import settings
from app.ingestion import audio as audio_mod


def test_no_local_fallback_fails_the_window_instead_of_skipping_speech(monkeypatch):
    monkeypatch.setattr(settings, "local_whisper_fallback", False)
    called = []
    monkeypatch.setattr(audio_mod, "transcribe_with_whisper", lambda *a, **k: called.append(1))
    with pytest.raises(RuntimeError, match="GROQ_API_KEY is not set"):
        audio_mod.transcribe("a.mp4", duration_ms=240_000, start_ms=0, end_ms=120_000)
    assert called == []
