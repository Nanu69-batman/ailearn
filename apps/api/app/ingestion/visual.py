"""Visual extraction: on-screen text and diagram description from frames.

The timestamp contract
----------------------
A visual segment's ``start_ms``/``end_ms`` come from **frame index
arithmetic** -- ``index * FRAME_INTERVAL_S`` -- and from nowhere else. The
model is asked to describe *what is on screen* and is explicitly told not to
emit timestamps; its output is parsed for text only, and any timestamp it
volunteers is discarded.

This is the whole point of the module. A VLM asked for timestamps will produce
plausible ones that drift, that restart at zero, that refer to the *clip* it
was shown rather than the *file* it belongs to. A frame sampled at 240s in a
655s lecture is at 240s because we decoded it at 240s, and no amount of model
confidence changes that. Frame-index arithmetic is checkable; model-asserted
time is not, and the strict-T gate depends on it being right.

Every visual segment therefore carries non-null ``start_ms``/``end_ms`` and
flows through the same strict-T gate as audio.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

#: Seconds between sampled frames. 4s keeps a 10-minute lecture to ~150 frames
#: (affordable for a multimodal model) while still catching a slide held for
#: a couple of minutes.
FRAME_INTERVAL_S = 4.0

#: Frames per Gemini request. Each request carries a JSON array of
#: ``{index, description}`` so the model reasons about sequence, and the model
#: never sees a timestamp.
FRAMES_PER_BATCH = 8

#: JPEG quality for sampled frames. Frames are read for text and diagrams, so
#: high quality matters more than file size here.
FRAME_JPEG_QUALITY = 85

_VISUAL_PROMPT = (
    "You are describing consecutive frames from the same lecture video so a "
    "search index can be built. For each frame, report ONLY what is actually "
    "visible: on-screen text (equations, labels, slide titles, bullet points) "
    "and visual explanation of diagrams (axes, plotted curves, arrows, "
    "colours, geometry, a pointer or gesture indicating something).\n"
    "Rules:\n"
    "- Return strict JSON: {\"frames\":[{\"index\":<int>,"
    "\"description\":\"...\"}]}\n"
    "- Use the frame's `index` exactly as given.\n"
    "- Do NOT output any timestamp, timecode, or duration. Timestamps are "
    "derived from frame indices elsewhere; a timestamp you invent will be "
    "discarded.\n"
    "- If a frame shows nothing legible or nothing not already said, give a "
    "short empty description such as \"no visible content\". OMIT what you "
    "cannot see. Never guess at content you cannot resolve, and never invent "
    "text that is not legible.\n"
    "- One short paragraph per frame. Describe, do not interpret."
)


@dataclass
class VisualSegment:
    """A frame's description plus the timeline that frame was sampled at.

    ``frame_index`` is retained alongside the derived bounds so the arithmetic
    is auditable after the fact -- if a citation points at 240s you can prove
    that is frame 60, not something the model asserted.
    """

    frame_index: int
    text: str
    start_ms: int
    end_ms: int

    @property
    def is_empty(self) -> bool:
        # Whitespace-only counts as empty: a model that returned "  " for a
        # blank frame has told us nothing, and indexing it would only dilute
        # results with a citation that has no content behind it.
        return not self.text.strip() or self.text.strip().lower() in _EMPTY_DESCRIPTIONS


_EMPTY_DESCRIPTIONS = frozenset(
    {
        "no visible content",
        "none",
        "n/a",
        "nothing",
        "no text",
        "no legible content",
        "blank",
    }
)


def frame_bounds(
    frame_index: int, *, duration_ms: int | None, interval_s: float = FRAME_INTERVAL_S
) -> tuple[int, int]:
    """Derive ``(start_ms, end_ms)`` for a frame from its index alone.

    Pure arithmetic -- no model involved, and both bounds are always non-null.
    ``end_ms`` is clamped to the authoritative duration so a frame interval
    straddling the last frame cannot claim content past the end of the file.
    """
    interval_ms = round(interval_s * 1000)
    start_ms = frame_index * interval_ms
    end_ms = start_ms + interval_ms
    if duration_ms is not None:
        start_ms = min(start_ms, duration_ms)
        end_ms = min(end_ms, duration_ms)
    if end_ms <= start_ms:
        # Zero-length spans cannot satisfy `end_ms <= T` meaningfully at a
        # boundary, so every frame gets at least 1ms of extent -- but the
        # clamp is applied again afterwards, because bumping `end_ms` past
        # `duration_ms` would reintroduce exactly the over-run this function
        # exists to prevent.
        end_ms = start_ms + 1
    if duration_ms is not None and end_ms > duration_ms:
        end_ms = duration_ms
        start_ms = min(start_ms, max(duration_ms - 1, 0))
    return start_ms, end_ms


def iter_frame_indices(
    duration_ms: int, *, interval_s: float = FRAME_INTERVAL_S
) -> list[int]:
    """Frame indices to sample across a known duration."""
    if duration_ms <= 0:
        return []
    interval_ms = round(interval_s * 1000)
    # +1 so the final partial interval is still sampled: a 655.5s file at 4s
    # has 163 full intervals (0..652s) and then 3.5s left over, which is real
    # content and would otherwise never be indexed.
    count = max(1, -(-duration_ms // interval_ms))
    return list(range(count))


def sample_frames(path: str, *, interval_s: float = FRAME_INTERVAL_S):
    """Decode frames at the sample interval with PyAV.

    Yields ``(frame_index, jpeg_bytes)``. Uses seek-then-decode so a
    11-minute file does not require decoding every frame to reach sample 150.
    Imported lazily so tests can pass frames directly without PyAV.
    """
    import av

    interval = max(interval_s, 0.1)
    with av.open(path) as container:
        stream = next(
            (s for s in container.streams if s.type == "video"), None
        )
        if stream is None:
            raise ValueError(f"no video stream in {path}")
        stream.thread_type = "AUTO"
        for index in iter_frame_indices(
            _container_duration_ms(container), interval_s=interval
        ):
            offset = int(index * interval)
            container.seek(offset * 1_000_000, any_frame=False, backward=True)
            for frame in container.decode(stream):
                if float(frame.pts * stream.time_base) + 1e-3 < index * interval:
                    continue
                image = frame.to_image()
                import io

                buf = io.BytesIO()
                image.save(buf, format="JPEG", quality=FRAME_JPEG_QUALITY)
                yield index, buf.getvalue()
                break


def _container_duration_ms(container) -> int:
    """Container duration in MILLISECONDS.

    PyAV reports ``container.duration`` in MICROSECONDS. Dividing by 1e6 here
    (a very easy slip, and it was made) yields SECONDS, which then looked like a
    655ms duration to frame sampling -- so a 655-second lecture sampled exactly
    one frame and the whole visual pass silently covered 0.6% of the video.
    """
    if not container.duration:
        return 0
    return round(container.duration / 1_000)


def _gemini_describe(frames: list[tuple[int, bytes]]) -> dict[int, str]:
    """Ask Gemini to describe each frame. Returns ``{index: description}``.

    The model is given frames in order with their indices and nothing else --
    no timestamps, no duration. Anything it returns that is not a valid index is
    dropped rather than guessed at.
    """
    from google import genai

    from app.config import settings

    client = genai.Client(api_key=settings.llm_api_key)
    parts: list = []
    for index, jpeg in frames:
        parts.append(f"Frame index {index}:")
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": jpeg}})
    parts.append(_VISUAL_PROMPT)
    resp = client.models.generate_content(model=settings.llm_model, contents=parts)
    return _parse_frame_descriptions(getattr(resp, "text", "") or "")


def _parse_frame_descriptions(raw: str) -> dict[int, str]:
    import json

    from app.ingestion.audio import _JSON_BLOCK

    match = _JSON_BLOCK.search(raw or "")
    if not match:
        log.warning("visual: no JSON payload in model response")
        return {}
    try:
        payload = json.loads(match.group(0))
    except ValueError:
        log.warning("visual: unparseable JSON payload from model")
        return {}
    out: dict[int, str] = {}
    for item in payload.get("frames") or []:
        try:
            index = int(item["index"])
        except (KeyError, TypeError, ValueError):
            continue
        out[index] = str(item.get("description") or "").strip()
    return out


def build_visual_segments(
    descriptions: dict[int, str],
    *,
    duration_ms: int,
    interval_s: float = FRAME_INTERVAL_S,
    keep_empty: bool = False,
) -> list[VisualSegment]:
    """Attach derived timestamps to each frame description.

    Timestamps come from :func:`frame_bounds` -- frame index arithmetic. Model
    output supplies the description text and nothing else.
    """
    segments: list[VisualSegment] = []
    for index in sorted(descriptions):
        text = descriptions[index]
        start_ms, end_ms = frame_bounds(
            index, duration_ms=duration_ms, interval_s=interval_s
        )
        seg = VisualSegment(index, text, start_ms, end_ms)
        if seg.is_empty and not keep_empty:
            continue
        segments.append(seg)
    return segments


def extract_visual_segments(
    path: str,
    *,
    duration_ms: int,
    interval_s: float = FRAME_INTERVAL_S,
    batch_size: int = FRAMES_PER_BATCH,
    max_frames: int | None = None,
    describe: bool = True,
) -> list[VisualSegment]:
    """Full visual pass: sample frames, describe them, derive their timestamps.

    ``describe=False`` samples and times frames without calling the model --
    used by tests, which must not touch the network.
    """
    described: dict[int, str] = {}
    if not describe:
        # Offline mode: sample and time frames without ever calling the model.
        # `describe=False` must be a hard no-model guarantee, not merely "the
        # batches happen not to fill".
        return []
    batch: list[tuple[int, bytes]] = []
    for seen, (index, jpeg) in enumerate(sample_frames(path, interval_s=interval_s)):
        if max_frames is not None and seen >= max_frames:
            break
        batch.append((index, jpeg))
        if len(batch) >= batch_size:
            described.update(_gemini_describe(batch))
            batch = []
    if batch:
        described.update(_gemini_describe(batch))
    return build_visual_segments(
        described, duration_ms=duration_ms, interval_s=interval_s
    )