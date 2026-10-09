"""Audio transcription with timestamps derived from the audio signal.

Two engines, one output shape:

* **faster-whisper** (primary) -- segment boundaries come from the ASR model
  reading the waveform, so they are as trustworthy as the transcription itself.
* **Gemini multimodal** (secondary) -- reads the audio/video track directly.
  Useful when whisper's model download is unavailable, but its timeline is
  *asserted by a model*, not measured, so it needs reconciliation (below).

Timeline reconciliation
-----------------------
A model's claimed end timestamp is not evidence. If Gemini says the last
utterance ends at 900s and the file is 655.5s long, indexing that verbatim
would place content 37% past the end of the media -- and, worse, would make
``end_ms`` meaningless to the strict-T gate. So:

* overshoot beyond :data:`GEMINI_TIMELINE_OVERSHOOT_TOLERANCE_S` -> **reject**
  the result entirely. Do not relax the tolerance to force a pass; a rejected
  transcription means no audio, which is recoverable, whereas a rescaled lie
  is not.
* overshoot within tolerance -> rescale onto the authoritative duration.

:func:`timestamp_agreement` reports the drift between the two engines so
"the transcript and the slides disagree" is visible in logs instead of silent.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass

from app.ingestion.duration import media_duration_ms

log = logging.getLogger(__name__)

#: How far past the true duration a model's timeline may run before we reject
#: it. 5% of the runtime: enough to absorb float rounding and a trailing
#: segment, far too small to hide a hallucinated timeline.
GEMINI_TIMELINE_OVERSHOOT_TOLERANCE_S = 0.05

#: Whisper model size. "tiny" keeps first-run cost sane for a course asset;
#: "small"/"medium" are drop-in replacements.
WHISPER_MODEL_SIZE = os.environ.get("WHISPER_MODEL_SIZE", "tiny")

#: Absolute floor for the overshoot budget, so a 3s clip still tolerates a
#: little rounding instead of rejecting on 0.05s.
_MIN_OVERSHOOT_BUDGET_S = 1.0


@dataclass
class TranscriptSegment:
    start_s: float
    end_s: float
    text: str

    @property
    def start_ms(self) -> int:
        # round() on a float already returns int; the extra cast would only
        # hide a bug if `start_s` were ever something else.
        return round(self.start_s * 1000)

    @property
    def end_ms(self) -> int:
        return round(self.end_s * 1000)


def _shim_pyav_metadata_errors() -> None:
    """Accept and drop the ``metadata_errors`` kwarg on newer PyAV builds.

    faster-whisper hard-codes ``av.open(file, metadata_errors="ignore")``;
    PyAV 19 removed the parameter. Rather than pinning a transitive dependency
    (which has no Python 3.14 wheels below 18), wrap ``av.open`` once so the
    call succeeds. Idempotent, and it only swallows the specific kwarg.
    """
    try:
        import av
    except ImportError:  # pragma: no cover - av ships with faster-whisper
        return
    if getattr(av.open, "_ailearn_shim", False):
        return

    original = av.open

    def _open(file, mode="r", metadata_errors=None, **kwargs):
        if metadata_errors is not None:
            log.debug("dropping metadata_errors=%r for PyAV %s", metadata_errors, av.__version__)
        return original(file, mode=mode, **kwargs)

    _open._ailearn_shim = True  # type: ignore[attr-defined]
    av.open = _open


def transcribe_with_whisper(
    path: str, *, model_size: str | None = None
) -> list[TranscriptSegment]:
    """Transcribe via faster-whisper. Segment bounds come from the audio.

    Imports and instantiates the model lazily: faster-whisper downloads model
    weights on first use, and tests must never trigger that. Tests inject a
    fake instead (see ``tests/test_audio_transcribe.py``).
    """
    from faster_whisper import WhisperModel

    model = WhisperModel(
        model_size or WHISPER_MODEL_SIZE, device="cpu", compute_type="int8"
    )
    # faster-whisper opens the file itself with av.open(..., metadata_errors=
    # "ignore"). PyAV >= 19 removed that kwarg, so the whole call raises
    # TypeError and audio silently degrades to the Gemini fallback. Shimmed
    # here rather than pinned in requirements because the alternative is a
    # floor on a transitive dependency with no Python 3.14 wheels.
    _shim_pyav_metadata_errors()
    segments, _info = model.transcribe(path, beam_size=5, vad_filter=True)
    out = [
        TranscriptSegment(float(s.start), float(s.end), (s.text or "").strip())
        for s in segments
        if (s.text or "").strip()
    ]
    log.info("whisper(%s) produced %d segment(s) for %s", model_size, len(out), path)
    return out


def _gemini_client():
    from google import genai

    from app.config import settings

    return genai.Client(api_key=settings.llm_api_key)


def transcribe_with_gemini(
    path: str, *, duration_s: float | None = None
) -> list[TranscriptSegment]:
    """Transcribe via Gemini multimodal, returning the model's RAW timeline.

    Deliberately does not reconcile -- :func:`reconcile_gemini_timeline` is
    separate and separately testable, so the rejection rule can be asserted
    without a network call.
    """
    client = _gemini_client()
    from app.config import settings

    prompt = (
        "Transcribe the spoken audio in this video. Return strict JSON: "
        '{"segments":[{"start":<seconds>,"end":<seconds>,"text":"..."}]}. '
        "Timestamps are seconds from the start of the video."
    )
    # Gemini takes an *uploaded file handle*, not a local path string. A bare
    # path comes back as "file uri and mime_type are required".
    upload = client.files.upload(file=path)
    resp = client.models.generate_content(
        model=settings.llm_model, contents=[upload, prompt]
    )
    return _parse_gemini_segments(getattr(resp, "text", "") or "")


_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


def _parse_gemini_segments(raw: str) -> list[TranscriptSegment]:
    import json

    match = _JSON_BLOCK.search(raw or "")
    if not match:
        return []
    try:
        payload = json.loads(match.group(0))
    except ValueError:
        log.warning("gemini transcript: unparseable JSON payload")
        return []
    out: list[TranscriptSegment] = []
    for item in payload.get("segments") or []:
        try:
            start = float(item["start"])
            end = float(item["end"])
        except (KeyError, TypeError, ValueError):
            continue
        text = str(item.get("text") or "").strip()
        if text:
            out.append(TranscriptSegment(start, end, text))
    return out


@dataclass
class TimelineVerdict:
    accepted: bool
    segments: list[TranscriptSegment]
    reason: str
    observed_end_s: float | None = None
    duration_s: float | None = None
    rescaled: bool = False


def reconcile_gemini_timeline(
    segments: list[TranscriptSegment], duration_s: float | None
) -> TimelineVerdict:
    """Validate a Gemini timeline against the authoritative duration.

    Reject when the claimed end exceeds duration beyond tolerance. Rescale
    (never extend) when it overshoots within tolerance. With no duration at all
    the result is accepted but marked -- duration being unavailable is logged
    loudly in :mod:`app.ingestion.duration`, and refusing all audio because a
    container header was odd would be worse than flagging it.
    """
    observed = max((s.end_s for s in segments), default=0.0)
    if not segments:
        return TimelineVerdict(True, [], "no segments returned")
    if not duration_s or duration_s <= 0:
        return TimelineVerdict(
            True, segments, "duration unknown; timeline unverified", observed, None
        )

    budget = max(duration_s * GEMINI_TIMELINE_OVERSHOOT_TOLERANCE_S, _MIN_OVERSHOOT_BUDGET_S)
    if observed > duration_s + budget:
        return TimelineVerdict(
            False,
            [],
            (
                f"gemini timeline end {observed:.2f}s exceeds duration "
                f"{duration_s:.2f}s by more than {budget:.2f}s tolerance; "
                "refusing to index"
            ),
            observed,
            duration_s,
        )
    if observed <= duration_s:
        return TimelineVerdict(True, segments, "within duration", observed, duration_s)

    scale = duration_s / observed
    rescaled = [
        TranscriptSegment(s.start_s * scale, min(s.end_s * scale, duration_s), s.text)
        for s in segments
    ]
    log.warning(
        "gemini timeline rescaled by %.4f (%0.2fs -> %.2fs) to fit duration",
        scale,
        observed,
        duration_s,
    )
    return TimelineVerdict(
        True, rescaled, "rescaled to authoritative duration", observed, duration_s, True
    )


def timestamp_agreement(
    primary: list[TranscriptSegment],
    secondary: list[TranscriptSegment],
) -> dict:
    """Compare two engines' timelines so drift is visible, not silent.

    Returns end-time deltas and the count of segments whose end differs by more
    than :data:`TIMESTAMP_AGREEMENT_TOLERANCE_MS`. A large drift is not a
    failure, but it means one engine's timeline should not be trusted without
    a human look -- hence a log line, not a silent preference.
    """

    def ends(segs: list[TranscriptSegment]) -> list[int]:
        return sorted(s.end_ms for s in segs)

    a, b = ends(primary), ends(secondary)
    if not a or not b:
        return {
            "comparable": False,
            "max_delta_ms": None,
            "drifting_segments": None,
            "note": "one engine returned no segments",
        }
    max_delta = max(abs(x - y) for x, y in zip(a, b))
    drifting = sum(1 for x, y in zip(a, b) if abs(x - y) > TIMESTAMP_AGREEMENT_TOLERANCE_MS)
    report = {
        "comparable": True,
        "max_delta_ms": max_delta,
        "drifting_segments": drifting,
        "segment_counts": [len(a), len(b)],
    }
    if max_delta > TIMESTAMP_AGREEMENT_TOLERANCE_MS:
        log.warning(
            "transcript timestamp disagreement: max delta %dms across %d "
            "segment(s) -- inspect before trusting either timeline",
            max_delta,
            drifting,
        )
    return report


#: Two engines are "in agreement" per-segment below this delta.
TIMESTAMP_AGREEMENT_TOLERANCE_MS = 2000


def transcribe(
    path: str,
    *,
    duration_ms: int | None = None,
    allow_gemini: bool = True,
) -> tuple[list[TranscriptSegment], dict]:
    """Primary whisper, secondary Gemini, reconciled.

    Returns ``(segments, signals)`` where ``signals`` carries
    ``timestamp_agreement`` so callers can persist the drift signal rather
    than have it evaporate into logs.
    """
    if duration_ms is None:
        duration_ms = media_duration_ms(path)
    duration_s = duration_ms / 1000.0 if duration_ms else None

    primary: list[TranscriptSegment] = []
    whisper_error: str | None = None
    try:
        primary = transcribe_with_whisper(path)
    except Exception as exc:  # noqa: BLE001 - fallback is the whole point
        whisper_error = f"{type(exc).__name__}: {exc}"
        log.warning("faster-whisper unavailable for %s (%s)", path, whisper_error)

    secondary: list[TranscriptSegment] = []
    if allow_gemini:
        try:
            raw = transcribe_with_gemini(path, duration_s=duration_s)
            verdict = reconcile_gemini_timeline(raw, duration_s)
            if verdict.accepted:
                secondary = verdict.segments
            else:
                # Rejected, not rescaled-into-compliance.
                log.error("gemini audio transcript rejected: %s", verdict.reason)
        except Exception as exc:  # noqa: BLE001 - secondary source is optional
            log.warning("gemini transcription failed for %s: %s", path, exc)

    chosen = primary or secondary
    signals = {
        "engine": "whisper" if primary else ("gemini" if secondary else "none"),
        "whisper_segments": len(primary),
        "gemini_segments": len(secondary),
        "whisper_error": whisper_error,
        "duration_ms": duration_ms,
        "timestamp_agreement": timestamp_agreement(primary, secondary),
    }
    return chosen, signals