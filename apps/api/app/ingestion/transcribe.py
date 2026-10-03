"""Gemini transcription for audio/video files (no local whisper available).

Uploads the media via the Files API, prompts the model for timestamped JSON
segments, validates them, then deletes the remote file. Never prints or logs
any API key.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

from app.cache import cache_enabled, get_cache
from app.config import settings
from app.ingestion.models import Segment

log = logging.getLogger(__name__)

_VIDEO_MIMES = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
}
_AUDIO_MIMES = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
}

TRANSCRIBE_PROMPT = (
    "Transcribe this {kind} titled {title!r} completely. "
    "Return ONLY a JSON array (no markdown fences, no commentary) where each "
    'element is {{"start_s": <float seconds>, "end_s": <float seconds>, '
    '"text": "<spoken words>"}}. '
    "Rules: segments must be 5-15 seconds each, start at 0, be contiguous and "
    "non-overlapping in ascending time order, and cover the FULL duration of "
    "the media with no gaps. Use plain text only. Every segment MUST have "
    "non-empty text: for silence, music, or other non-speech audio, write "
    'the text as "[non-speech]".'
)

_REPAIR_SUFFIX = (
    " Your previous response was rejected for this reason: {error}. "
    "Return the corrected full JSON array (same rules, non-empty text "
    "everywhere)."
)


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _to_segments(items: list[dict]) -> list[Segment]:
    return [
        Segment(
            text=item["text"],
            source_type="video",
            start_ms=int(item["start_s"] * 1000),
            end_ms=int(item["end_s"] * 1000),
            index=i,
        )
        for i, item in enumerate(items)
    ]
def media_mime_type(path: str | Path) -> str:
    """Detect a Files-API mime type from the suffix (video vs audio)."""
    suffix = Path(path).suffix.lower()
    if suffix in _VIDEO_MIMES:
        return _VIDEO_MIMES[suffix]
    if suffix in _AUDIO_MIMES:
        return _AUDIO_MIMES[suffix]
    return "video/mp4" if suffix in (".avi", ".m4v") else "audio/mpeg"


def _is_audio_path(path: str | Path) -> bool:
    return Path(path).suffix.lower() in _AUDIO_MIMES


def validate_transcript_payload(
    data: Any, duration_s: float | None = None
) -> list[dict[str, Any]]:
    """Validate raw model JSON into normalized [{start_s, end_s, text}].

    Raises ValueError on: non-list/empty payload, missing keys, empty text,
    start >= end, non-monotonic/non-contiguous order, internal gaps,
    negative times, absurd segment lengths, end beyond the known duration,
    or failure to cover the full duration (first start > 5s or last end
    short by > 10s). Raises TypeError when a segment is not an object.
    """
    if not isinstance(data, list) or not data:
        raise ValueError("transcript payload must be a non-empty JSON array")
    cleaned: list[dict[str, Any]] = []
    prev_start = -1.0
    prev_end = 0.0
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise TypeError(f"segment {i} must be an object")
        try:
            start = float(item["start_s"])
            end = float(item["end_s"])
            text = str(item["text"]).strip()
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"segment {i} has invalid fields") from exc
        if not text:
            raise ValueError(f"segment {i} has empty text")
        if start < 0 or end <= start:
            raise ValueError(f"segment {i} has invalid time range")
        if start < prev_start:
            raise ValueError(f"segment {i} is not in ascending time order")
        if start < prev_end - 0.5:
            raise ValueError(f"segment {i} overlaps the previous segment")
        if i > 0 and start > prev_end + 2.0:
            raise ValueError(f"segment {i} leaves a gap in coverage")
        length = end - start
        # Prompt asks for 5-15s; tolerate up to 45s since the chunker bounds
        # windows independently (force-close at 90s). Beyond that is garbage.
        if length < 2.0 or length > 45.0:
            raise ValueError(f"segment {i} length {length:.1f}s outside 2-45s")
        if duration_s is not None and end > duration_s + 2.0:
            raise ValueError(f"segment {i} ends beyond the media duration")
        cleaned.append({"start_s": start, "end_s": end, "text": text})
        prev_start = start
        prev_end = end
    if duration_s is not None:
        if cleaned[0]["start_s"] > 5.0:
            raise ValueError("transcript does not start near 0")
        if cleaned[-1]["end_s"] < duration_s - 10.0:
            raise ValueError("transcript does not cover the full duration")
    return cleaned


def parse_transcript_json(raw: str, duration_s: float | None = None) -> list[dict]:
    """Strip fences, JSON-parse, and validate a model response."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = [ln for ln in lines if not ln.strip().startswith("```")]
        text = "\n".join(lines).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"transcript response is not valid JSON: {exc}") from exc
    return validate_transcript_payload(data, duration_s)


def transcribe_with_gemini(
    media_path: str | Path, title: str, duration_s: float | None = None
) -> list[Segment]:
    """Transcribe a local media file with Gemini; returns timestamped Segments."""
    api_key = settings.llm_api_key or settings.embedding_api_key
    if not api_key:
        raise RuntimeError("LLM_API_KEY not set; transcribe_with_gemini unavailable")
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise RuntimeError("google-genai package not installed") from exc

    path = Path(media_path)
    cache_key = "transcript:" + settings.llm_model + ":" + _file_sha256(path)
    if cache_enabled():
        cached = get_cache("transcripts").get(cache_key)
        if cached is not None:
            log.info("transcript cache hit for %s", path.name)
            items = validate_transcript_payload(cached, duration_s)
            return _to_segments(items)
    kind = "audio" if _is_audio_path(path) else "video"
    client = genai.Client(api_key=api_key)
    remote = client.files.upload(file=str(path))
    try:
        for _ in range(60):
            state = client.files.get(name=remote.name)
            if state.state.name == "ACTIVE":
                break
            if state.state.name == "FAILED":
                raise RuntimeError("media processing failed server-side")
            time.sleep(2.0)
        prompt = TRANSCRIBE_PROMPT.format(kind=kind, title=title)
        items: list[dict] | None = None
        last_error = ""
        for _attempt in range(2):
            resp = client.models.generate_content(
                model=settings.llm_model,
                contents=[prompt + last_error, remote],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json", temperature=0.0
                ),
            )
            try:
                items = parse_transcript_json(resp.text or "", duration_s)
                break
            except (ValueError, TypeError) as exc:
                log.warning("transcript validation failed: %s; retrying", exc)
                last_error = _REPAIR_SUFFIX.format(error=exc)
        if items is None:
            raise ValueError(f"transcript failed validation twice: {last_error}")
        if cache_enabled():
            get_cache("transcripts").set(cache_key, items)
    finally:
        try:
            client.files.delete(name=remote.name)
        except Exception as exc:  # noqa: BLE001 - cleanup best-effort
            log.warning("remote file cleanup failed: %s", type(exc).__name__)
    segments = _to_segments(items)
    log.info("transcribed %s into %d segments", title, len(segments))
    return segments
