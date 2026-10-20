"""Visual extraction: on-screen text and diagram description from frames.

The timestamp contract
----------------------
A visual segment's ``start_ms``/``end_ms`` come from **frame index
arithmetic** -- ``index * FRAME_INTERVAL_S`` -- and from nowhere else. The
model is told *which* frame it is looking at because it has to, but the index
it echoes back is a **claim, not a fact**. It is accepted only after it is
matched against the set of indices the sampler actually yielded; an index that
does not name a sampled frame discards the whole batch rather than being
trusted, clamped, or rounded to a neighbour.

This is the whole point of the module. A VLM asked for timestamps will produce
plausible ones that drift, that restart at zero, that refer to the *clip* it
was shown rather than the *file* it belongs to. A frame sampled at 240s in a
655s lecture is at 240s because we decoded it at 240s, and no amount of model
confidence changes that. Frame-index arithmetic is checkable; model-asserted
time is not, and the strict-T gate depends on it being right.

The same reasoning applies to the index itself, which is why it is
re-derived from the sampler rather than read out of the reply. A reply that
labels the frame decoded at 650s as ``index: 5`` would otherwise be stored at
20000ms, and a learner paused at T=20s would retrieve content from 10:50 --
a spoiler delivered by the model's own output.

Every visual segment therefore carries non-null ``start_ms``/``end_ms`` and
flows through the same strict-T gate as audio.
"""

from __future__ import annotations

import io
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from app.ingestion import backoff as backoff_mod

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

#: Minimum seconds between two Gemini describe requests, measured start to
#: start.
#:
#: The free tier allows roughly 15 ``generate_content`` requests per minute. A
#: 655s lecture at a 4s interval is 164 frames = 21 batches, so a burst of 21
#: unspaced calls *will* be rate limited part-way through. Pacing is what stops
#: the tail of the video from being the part that gets dropped; the bounded
#: retry in :func:`_describe_with_retry` covers what pacing alone cannot.
DESCRIBE_MIN_INTERVAL_S = 4.0

#: Attempts for one batch before it is recorded as a failure. Backoff is
#: exponential from :data:`DESCRIBE_BACKOFF_S`, capped at :data:`DESCRIBE_BACKOFF_CAP_S`.
DESCRIBE_MAX_ATTEMPTS = backoff_mod.DEFAULT_MAX_ATTEMPTS

#: Exponential backoff base and ceiling for a rate-limited/transient failure.
DESCRIBE_BACKOFF_S = backoff_mod.DEFAULT_BACKOFF_S
DESCRIBE_BACKOFF_CAP_S = backoff_mod.DEFAULT_BACKOFF_CAP_S

#: Extra attempts when a batch returns *nothing usable* rather than an error.
#:
#: A truncated or garbled response is indistinguishable from a rate-limit hit
#: from the caller's side, and quietly discarding those frames is exactly the
#: failure this module exists to prevent -- so it is retried, then reported.
DESCRIBE_EMPTY_RETRIES = 2


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


#: Worker threads running describe batches. Call STARTS are still spaced by
#: :data:`DESCRIBE_MIN_INTERVAL_S` across all workers, and a retryable failure
#: on any worker pauses all of them (shared not-before), so this overlaps
#: latency without raising the request rate. Env: ``VISION_CONCURRENCY``.
VISION_CONCURRENCY = max(1, _env_int("VISION_CONCURRENCY", 3))

#: Skip frames that look the same as the last KEPT frame. Env: ``VISUAL_DEDUPE``.
DEDUPE_ENABLED = _env_bool("VISUAL_DEDUPE", True)

#: Near-duplicate test: frames are reduced to 64x64 grayscale and split into an
#: 8x8 grid; a frame is "changed" if ANY cell's mean absolute pixel difference
#: (0-255) from the last kept frame exceeds this. Per-cell (not whole-image) so
#: a small localised change -- one new line on a board -- still counts.
#: TRADEOFF: the threshold is deliberately low (measured on a real lecture: static-frame noise 0.1-0.4, genuine incremental board changes 1.7-5.2), but
#: a change smaller than it (a single faint character, a cursor move) is merged
#: into the previous frame's description rather than described separately. The
#: forced keep below bounds how long such a change can go unseen.
#: Env: ``VISUAL_DEDUPE_CELL_THRESHOLD``.
DEDUPE_CELL_THRESHOLD = _env_float("VISUAL_DEDUPE_CELL_THRESHOLD", 1.0)

#: Safety net: at least every Nth sampled frame since the last kept one is kept
#: regardless of similarity (5 -> at most 4 skipped, i.e. 20 s at a 4 s
#: interval). Env: ``VISUAL_DEDUPE_FORCE_KEEP_EVERY``.
DEDUPE_FORCE_KEEP_EVERY = max(1, _env_int("VISUAL_DEDUPE_FORCE_KEEP_EVERY", 5))

#: Long-side cap (px) for frames SENT to the model; 0 disables. Frames already
#: smaller are sent untouched. Env: ``VISUAL_FRAME_MAX_SIDE``.
SEND_MAX_SIDE_PX = _env_int("VISUAL_FRAME_MAX_SIDE", 768)

#: JPEG quality for downscaled frames sent to the model.
#: Env: ``VISUAL_SEND_JPEG_QUALITY``.
SEND_JPEG_QUALITY = _env_int("VISUAL_SEND_JPEG_QUALITY", 80)

_DEDUPE_GRID = 8
_DEDUPE_SIDE = 64


def _is_retryable(exc: BaseException) -> bool:
    """Alias of :func:`app.ingestion.backoff.is_retryable`, kept for readability."""
    return backoff_mod.is_retryable(exc)

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
    "- 1-2 concise factual sentences per frame. Describe, do not interpret. "
    "Still name every exact equation, label, title, colour and shape you can "
    "read -- they are what search will match on."
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


def window_frame_indices(
    duration_ms: int,
    *,
    interval_s: float = FRAME_INTERVAL_S,
    start_ms: int | None = None,
    end_ms: int | None = None,
) -> list[int]:
    """Indices for ``[start_ms, end_ms)``: ``[ceil(start/i), ceil(min(end,dur)/i))``.

    With no range this IS :func:`iter_frame_indices`. Windows aligned to
    multiples of the interval partition the monolithic index set exactly.
    """
    if start_ms is None and end_ms is None:
        return iter_frame_indices(duration_ms, interval_s=interval_s)
    if duration_ms <= 0:
        return []
    interval_ms = round(interval_s * 1000)
    lo = -(-max(start_ms or 0, 0) // interval_ms)
    hi_ms = duration_ms if end_ms is None else min(end_ms, duration_ms)
    hi = -(-hi_ms // interval_ms)
    return list(range(lo, max(hi, lo)))


def sample_frames(
    path: str,
    *,
    interval_s: float = FRAME_INTERVAL_S,
    start_ms: int | None = None,
    end_ms: int | None = None,
):
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
        for index in window_frame_indices(
            _container_duration_ms(container),
            interval_s=interval,
            start_ms=start_ms,
            end_ms=end_ms,
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
    no timestamps, no duration. The index is in the prompt because the model
    cannot describe "the fourth image" any other way; what it is *not* is a
    source of new frames. :func:`_validated_descriptions` checks every index in
    this result against the indices the sampler actually yielded, and discards
    anything that does not match.
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
    """Parse a describe reply into ``{index: description}``.

    Pure JSON extraction -- it does **not** decide which indices are real. A
    repeated index keeps its first occurrence rather than the last, so a reply
    that labels two images ``7`` cannot quietly overwrite one with the other.
    Deciding which indices may become timestamps is
    :func:`_validated_descriptions`, the one place that knows both what the
    model said and what the sampler decoded.
    """
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
        if not isinstance(item, dict):
            continue
        try:
            index = int(item["index"])
        except (KeyError, TypeError, ValueError):
            continue
        if index in out:
            # Ambiguous, not merely redundant: two different frames claiming
            # one index means the model lost track of which is which. The
            # validator reports the mismatch, so it is not hidden by an
            # overwrite either.
            log.error("visual: index %s appears twice in one reply", index)
            continue
        out[index] = str(item.get("description") or "").strip()
    return out


def _validated_descriptions(
    result: dict[int, str], sent: set[int]
) -> tuple[dict[int, str], list[str]]:
    """Keep only descriptions whose index names a frame we actually sampled.

    This is the trust boundary. ``result`` is keyed by integers the *model*
    chose; ``sent`` is what the *sampler* decoded. Only an index present in
    both may become a timestamp, because only those two agreeing means the
    model was describing the frame we handed it.

    Anything else is dropped and described in the returned notes:

    * an index outside ``sent`` -- the model invented or misremembered a frame.
      Clamping it onto the last frame, or shifting it onto a neighbour, would
      file a real description against a moment nobody observed it at, which is
      exactly how a frame decoded at 650s becomes retrievable at T=20s.
    * a repeated index -- collapsed by the parser and reported here, so the
      ambiguity is visible in the pass report rather than resolved by
      last-write-wins.

    Returns ``(kept, notes)``. Entries that do match are untouched: one bad
    label costs that entry, not the rest of the batch.
    """
    stray = sorted(i for i in result if i not in sent)
    if not stray:
        return result, []
    for index in stray:
        del result[index]
    note = (
        f"model returned index(es) {stray} that were not sampled; their "
        "descriptions were discarded rather than attached to a neighbouring "
        "frame"
    )
    return result, [note]


def build_visual_segments(
    descriptions: dict[int, str],
    *,
    duration_ms: int,
    interval_s: float = FRAME_INTERVAL_S,
    keep_empty: bool = False,
    sampled_indices: list[int] | None = None,
) -> tuple[list[VisualSegment], list[int]]:
    """Attach derived timestamps to each frame description.

    Timestamps come from :func:`frame_bounds` -- frame index arithmetic. Model
    output supplies the description text and nothing else.

    ``sampled_indices`` is the sampler's own list of the frames it decoded, and
    it is the authority on *which indices exist*. A description whose index is
    not in that set yields **no segment at all**: it is not clamped down to the
    end of the video, not rounded to a neighbouring frame, and not stored.
    Guessing where an unplaceable description belonged is precisely the spoiler
    this module exists to prevent -- it is a description of something we saw,
    filed against a time we did not observe it at.

    Unmatched indices are surfaced by :func:`extract_visual_pass` (which owns
    the sampler's list and the report) rather than returned here, so that this
    function stays a pure timestamp derivation.
    """
    allowed = set(sampled_indices) if sampled_indices is not None else None
    segments: list[VisualSegment] = []
    for index in sorted(descriptions):
        if allowed is not None and index not in allowed:
            continue
        text = descriptions[index]
        start_ms, end_ms = frame_bounds(
            index, duration_ms=duration_ms, interval_s=interval_s
        )
        seg = VisualSegment(index, text, start_ms, end_ms)
        if seg.is_empty and not keep_empty:
            continue
        segments.append(seg)
    return segments


@dataclass
class VisualPassReport:
    """What the visual pass actually managed to describe.

    The shipped bug was not that descriptions were wrong -- it was that a pass
    covering 24% of the file reported exactly the same "129 chunks" success as
    a complete one. This dataclass exists so the caller can *tell the
    difference*, and so the difference is visible in the job record rather
    than only in a log line nobody reads.
    """

    segments: list[VisualSegment] = field(default_factory=list)
    #: Every index the timeline says should have been sampled.
    expected_indices: list[int] = field(default_factory=list)
    #: Indices PyAV actually yielded.
    sampled_indices: list[int] = field(default_factory=list)
    #: Indices the model actually returned a description for.
    described_indices: list[int] = field(default_factory=list)
    #: One human-readable line per batch that could not be described.
    failures: list[str] = field(default_factory=list)
    #: A caller-supplied ``max_frames`` cut the walk short. Always degraded,
    #: never silently "complete", even if every frame it did reach succeeded.
    truncated: bool = False
    #: The authoritative timeline the pass was timed against.
    duration_ms: int = 0
    #: ``False`` when no describe pass ran at all (``describe=False``), which is
    #: "skipped" rather than "failed" -- nothing was attempted to go wrong.
    attempted: bool = True
    #: Sampled frames skipped as near-duplicates of the last kept frame. They
    #: are covered by that frame's description span, never "failed".
    skipped_indices: list[int] = field(default_factory=list)
    #: Subset of :attr:`skipped_indices` whose owning (kept) frame WAS described.
    #: If the owner's batch failed, its skipped frames stay uncovered.
    duplicate_covered_indices: list[int] = field(default_factory=list)
    #: Per-window timing: sample_s, vision_calls, retry_wait_s, total_s, ...
    timings: dict = field(default_factory=dict)

    @property
    def skipped_duplicates(self) -> int:
        return len(self.skipped_indices)

    @property
    def covered_indices(self) -> list[int]:
        """Expected indices that came back with a description (directly, or by
        being a near-duplicate inside a described frame's span)."""
        seen = set(self.described_indices) | set(self.duplicate_covered_indices)
        return [i for i in self.expected_indices if i in seen]

    @property
    def status(self) -> str:
        """``complete`` | ``partial`` | ``failed`` | ``skipped``.

        ``complete`` means the whole timeline was described. Anything short of
        that is named, never rounded up.
        """
        if not self.attempted or not self.expected_indices:
            return "skipped"
        if not self.covered_indices:
            return "failed"
        if self.truncated or len(self.covered_indices) < len(self.expected_indices):
            return "partial"
        return "complete"

    @property
    def coverage_pct(self) -> float:
        """Share of the timeline that got a description. The honesty metric."""
        if not self.expected_indices:
            return 0.0
        return round(100.0 * len(self.covered_indices) / len(self.expected_indices), 1)

    @property
    def span_pct(self) -> float:
        """Share of the duration reachable by at least one described frame.

        This is the number that made the shipped bug visible: the stored
        ``res_95f6dc70e36a`` reported 24.4% -- 160 seconds of a 655-second
        lecture described as though it were the whole thing. Distinct from
        :attr:`coverage_pct`: describing frames 0-39 *and* 163 gives 100%
        span but 25% coverage, and quoting the span alone would hide the hole
        in the middle.
        """
        if not self.segments or self.duration_ms <= 0:
            return 0.0
        return round(
            100.0 * max(s.end_ms for s in self.segments) / self.duration_ms, 1
        )

    @property
    def warnings(self) -> list[str]:
        out: list[str] = []
        if self.truncated:
            out.append(
                f"visual pass stopped after {len(self.sampled_indices)} of "
                f"{len(self.expected_indices)} frames (frame cap); the rest of "
                "the video has no on-screen description"
            )
        status = self.status
        if status == "failed":
            out.append(
                f"visual pass described 0 of {len(self.expected_indices)} frames"
            )
        elif status == "partial":
            out.append(
                f"visual pass described {len(self.covered_indices)} of "
                f"{len(self.expected_indices)} frames "
                f"({self.coverage_pct}% of the timeline)"
            )
        out.extend(self.failures)
        return out


class _CallGate:
    """Shared pacing + backoff for all describe workers of one pass.

    * ``wait_turn`` enforces a minimum spacing between call STARTS across every
      worker (the free-tier request rate), and never lets a call start before
      the shared ``not_before``.
    * ``penalize`` is called when any worker sees a retryable failure: it moves
      ``not_before`` forward so ALL workers pause, instead of each retrying on
      its own clock.
    * Batches take their first turn in submission order (``seq``) so call
      starts are deterministic.
    """

    def __init__(self, min_interval_s: float, *, clock=time.monotonic, sleep=time.sleep):
        self.min_interval_s = max(0.0, min_interval_s)
        self._clock = clock
        self._sleep = sleep
        self._cond = threading.Condition()
        self._last_start: float | None = None
        self._not_before = 0.0
        self._next_seq = 0
        self.retry_wait_s = 0.0

    @property
    def not_before(self) -> float:
        return self._not_before

    def penalize(self, delay_s: float) -> None:
        with self._cond:
            now = self._clock()
            target = now + max(0.0, delay_s)
            if target > self._not_before:
                # Count only the extension beyond an already-pending pause.
                self.retry_wait_s += target - max(self._not_before, now)
                self._not_before = target

    def wait_turn(self, seq: int | None = None) -> None:
        if seq is not None:
            with self._cond:
                while self._next_seq != seq:
                    self._cond.wait()
        try:
            while True:
                with self._cond:
                    now = self._clock()
                    ready = self._not_before
                    if self._last_start is not None:
                        ready = max(ready, self._last_start + self.min_interval_s)
                    if now >= ready:
                        self._last_start = now
                        return
                    wait = ready - now
                self._sleep(wait)
        finally:
            if seq is not None:
                with self._cond:
                    self._next_seq = max(self._next_seq, seq + 1)
                    self._cond.notify_all()


class _PassStats:
    """Thread-safe per-pass counters feeding ``VisualPassReport.timings``."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.latencies: list[float] = []
        self.call_bytes: list[int] = []
        self.call_frames: list[int] = []

    def record_call(self, latency_s: float, nbytes: int, nframes: int) -> None:
        with self._lock:
            self.latencies.append(round(latency_s, 2))
            self.call_bytes.append(nbytes)
            self.call_frames.append(nframes)


#: Per-worker-thread pass context (gate, seq, stats). Lets the pool call
#: ``_describe_with_retry(items)`` with its original one-argument shape.
_ctx = threading.local()


def _describe_with_retry(
    frames: list[tuple[int, bytes]],
    *,
    max_attempts: int | None = None,
    sleep=time.sleep,
    gate: _CallGate | None = None,
    seq: int | None = None,
    stats: _PassStats | None = None,
) -> tuple[dict[int, str], str]:
    """Describe one batch, surviving rate limits. Returns ``(result, error)``.

    Never raises: a batch that cannot be described is reported as an error
    string so the caller can keep going and *record the gap*. Letting the
    exception out is what turned a partial pass into a truncated one with no
    trace.

    With a ``gate`` the whole batch is retried after a SHARED pause (all
    workers wait); without one (direct callers/tests) it sleeps locally.
    """
    gate = gate if gate is not None else getattr(_ctx, "gate", None)
    seq = seq if seq is not None else getattr(_ctx, "seq", None)
    stats = stats if stats is not None else getattr(_ctx, "stats", None)
    attempts = max_attempts or DESCRIBE_MAX_ATTEMPTS
    empty_attempts = min(attempts, DESCRIBE_EMPTY_RETRIES + 1)
    last = "no description attempt was made"
    nbytes = sum(len(j) for _, j in frames)

    def _pause(delay: float) -> None:
        if gate is not None:
            gate.penalize(delay)
        else:
            sleep(delay)

    for attempt in range(1, attempts + 1):
        if gate is not None:
            gate.wait_turn(seq if attempt == 1 else None)
        t0 = time.monotonic()
        try:
            result = _gemini_describe(frames)
        except Exception as exc:  # noqa: BLE001 - classified, never propagated
            if stats is not None:
                stats.record_call(time.monotonic() - t0, nbytes, len(frames))
            last = f"{type(exc).__name__}: {exc}"
            if not _is_retryable(exc) or attempt >= attempts:
                log.error("visual: describe batch abandoned after %d attempt(s): %s",
                          attempt, last)
                return {}, last
            delay = min(DESCRIBE_BACKOFF_CAP_S, DESCRIBE_BACKOFF_S ** attempt)
            log.warning("visual: describe attempt %d/%d failed (%s); retrying in %.0fs",
                        attempt, attempts, last, delay)
            _pause(delay)
            continue
        if stats is not None:
            stats.record_call(time.monotonic() - t0, nbytes, len(frames))
        if result:
            return result, ""
        last = "model returned no usable frame descriptions"
        if attempt >= empty_attempts:
            log.error("visual: %s for indices %s", last,
                      [i for i, _ in frames])
            return {}, last
        _pause(min(DESCRIBE_BACKOFF_CAP_S, DESCRIBE_BACKOFF_S ** attempt))
    return {}, last


# --- frame preparation: near-duplicate detection and shrinking ---------------


def _open_image(jpeg: bytes):
    """Decode ``jpeg`` with PIL, or ``None`` if it is not a decodable image."""
    try:
        from PIL import Image

        img = Image.open(io.BytesIO(jpeg))
        img.load()
        return img
    except Exception:  # noqa: BLE001 - undecodable => treated as "changed"
        return None


def _signature(img) -> bytes | None:
    """64x64 grayscale pixels of ``img`` (row-major), or ``None``."""
    if img is None:
        return None
    try:
        from PIL import Image

        return img.convert("L").resize(
            (_DEDUPE_SIDE, _DEDUPE_SIDE), Image.BOX
        ).tobytes()
    except Exception:  # noqa: BLE001
        return None


def frames_differ(
    sig: bytes | None,
    ref: bytes | None,
    *,
    threshold: float | None = None,
) -> bool:
    """True if ANY 8x8-grid cell's mean abs diff exceeds the threshold.

    Unknown signatures (undecodable frames) count as different: when in doubt,
    send the frame.
    """
    if sig is None or ref is None or len(sig) != len(ref):
        return True
    limit = DEDUPE_CELL_THRESHOLD if threshold is None else threshold
    side, cell = _DEDUPE_SIDE, _DEDUPE_SIDE // _DEDUPE_GRID
    for gy in range(_DEDUPE_GRID):
        for gx in range(_DEDUPE_GRID):
            total = 0
            for y in range(gy * cell, (gy + 1) * cell):
                off = y * side + gx * cell
                a = sig[off : off + cell]
                b = ref[off : off + cell]
                total += sum(abs(p - q) for p, q in zip(a, b))
            if total / (cell * cell) > limit:
                return True
    return False


def shrink_frame(jpeg: bytes, img=None, *, max_side: int | None = None) -> bytes:
    """Downscale so the long side is <= ``max_side`` (JPEG q=SEND_JPEG_QUALITY).

    Frames already within the cap, undecodable frames, and ``max_side<=0`` are
    returned unchanged.
    """
    limit = SEND_MAX_SIDE_PX if max_side is None else max_side
    if limit <= 0:
        return jpeg
    img = img if img is not None else _open_image(jpeg)
    if img is None or max(img.size) <= limit:
        return jpeg
    try:
        from PIL import Image

        scale = limit / max(img.size)
        size = (max(1, round(img.width * scale)), max(1, round(img.height * scale)))
        out = io.BytesIO()
        img.convert("RGB").resize(size, Image.LANCZOS).save(
            out, format="JPEG", quality=SEND_JPEG_QUALITY
        )
        return out.getvalue()
    except Exception:  # noqa: BLE001
        return jpeg


def extract_visual_pass(
    path: str,
    *,
    duration_ms: int,
    interval_s: float = FRAME_INTERVAL_S,
    batch_size: int = FRAMES_PER_BATCH,
    max_frames: int | None = None,
    describe: bool = True,
    start_ms: int | None = None,
    end_ms: int | None = None,
) -> VisualPassReport:
    """Full visual pass with an honest account of what it covered.

    ``max_frames`` defaults to *no cap*. The shipped default of 40 truncated a
    655-second lecture to its first 160 seconds -- 40 frames at a 4s interval
    -- and reported the result as a successful ingest. A cap is still available
    for callers that genuinely want one, but it now marks the pass
    :attr:`VisualPassReport.truncated`, which forces a non-``complete`` status.

    Near-duplicate frames are not sent: the last KEPT frame's description
    covers the span up to the next kept frame, and its segment's ``end_ms``
    grows to the end of the last frame it covers (clamped to the duration and
    the window end). Timestamps still derive from sampler indices only, and a
    later ``end_ms`` only makes the strict ``end_ms <= T`` gate stricter.
    """
    t_start = time.monotonic()
    # Windowed: expected coverage is ONLY this window's indices, so a finished
    # window reports complete rather than partial.
    expected = window_frame_indices(
        duration_ms, interval_s=interval_s, start_ms=start_ms, end_ms=end_ms
    )
    if not describe:
        # Offline mode: no sampling and no model call at all. `describe=False`
        # must stay a hard no-model guarantee, not merely "the batches happen
        # not to fill".
        return VisualPassReport(
            expected_indices=expected, duration_ms=duration_ms, attempted=False
        )

    described: dict[int, str] = {}
    sampled: list[int] = []
    failures: list[str] = []
    truncated = False
    batch: list[tuple[int, bytes]] = []
    skipped: list[int] = []
    owner: dict[int, int] = {}  # skipped index -> kept index covering it
    last_covered: dict[int, int] = {}  # kept index -> last frame index it covers
    stats = _PassStats()
    gate = _CallGate(DESCRIBE_MIN_INTERVAL_S)
    concurrency = max(1, VISION_CONCURRENCY)
    futures: list = []
    frames_sent = 0
    raw_bytes = sent_bytes = 0
    sample_s = 0.0

    def _run_batch(items: list[tuple[int, bytes]], seq: int):
        _ctx.gate, _ctx.seq, _ctx.stats = gate, seq, stats
        try:
            result, error = _describe_with_retry(items)
        finally:
            _ctx.gate = _ctx.seq = _ctx.stats = None
        span = f"{items[0][0]}-{items[-1][0]}"
        if error:
            return span, {}, [], error
        # The sampler's indices are the only ones that may become timestamps.
        # A reply naming an index we never decoded cannot be placed on the
        # timeline, so that entry is dropped and recorded -- never reassigned to
        # a neighbouring frame, never clamped onto the last one. The other
        # frames described in the same reply are genuine and are kept.
        kept, notes = _validated_descriptions(result, {i for i, _ in items})
        return span, kept, notes, ""

    sampler_kwargs: dict = {}
    if start_ms is not None or end_ms is not None:
        sampler_kwargs = {"start_ms": start_ms, "end_ms": end_ms}

    ref_sig: bytes | None = None
    kept_index: int | None = None
    since_kept = 0
    with ThreadPoolExecutor(
        max_workers=concurrency, thread_name_prefix="vision"
    ) as pool:
        frame_iter = iter(sample_frames(path, interval_s=interval_s, **sampler_kwargs))
        seen = 0
        while True:
            t0 = time.monotonic()
            try:
                index, jpeg = next(frame_iter)
            except StopIteration:
                sample_s += time.monotonic() - t0
                break
            sample_s += time.monotonic() - t0
            if max_frames is not None and seen >= max_frames:
                truncated = True
                log.error(
                    "visual: frame cap %d truncated the walk of %s -- %d of %d "
                    "frames will never be described",
                    max_frames, Path(path).name, min(seen, len(expected)),
                    len(expected),
                )
                break
            seen += 1
            sampled.append(index)

            img = _open_image(jpeg) if (DEDUPE_ENABLED or SEND_MAX_SIDE_PX > 0) else None
            keep = True
            sig = None
            if DEDUPE_ENABLED and kept_index is not None:
                sig = _signature(img)
                if since_kept + 1 < DEDUPE_FORCE_KEEP_EVERY and not frames_differ(
                    sig, ref_sig
                ):
                    keep = False
            elif DEDUPE_ENABLED:
                sig = _signature(img)
            if not keep:
                since_kept += 1
                skipped.append(index)
                owner[index] = kept_index  # type: ignore[assignment]
                last_covered[kept_index] = index  # type: ignore[index]
                continue
            kept_index, ref_sig, since_kept = index, sig, 0
            last_covered[index] = index
            small = shrink_frame(jpeg, img)
            raw_bytes += len(jpeg)
            sent_bytes += len(small)
            frames_sent += 1
            batch.append((index, small))
            if len(batch) >= batch_size:
                futures.append(pool.submit(_run_batch, batch, len(futures)))
                batch = []
        if batch:
            futures.append(pool.submit(_run_batch, batch, len(futures)))
        # Merge in submission (= index) order. result() re-raises any worker
        # exception rather than swallowing it.
        for fut in futures:
            span, kept, notes, error = fut.result()
            if error:
                failures.append(f"frames {span}: {error}")
                continue
            for note in notes:
                failures.append(f"frames {span}: {note}")
            described.update(kept)

    report = VisualPassReport(
        expected_indices=expected,
        sampled_indices=sampled,
        described_indices=sorted(described),
        failures=failures,
        truncated=truncated,
        duration_ms=duration_ms,
        skipped_indices=skipped,
        duplicate_covered_indices=[i for i in skipped if owner[i] in described],
    )
    report.segments = build_visual_segments(
        described,
        duration_ms=duration_ms,
        interval_s=interval_s,
        # Belt and braces: the sampler is the authority on which indices exist.
        sampled_indices=sampled,
    )
    # Extend each kept frame's segment over the near-duplicates it covers. The
    # new end is frame_bounds() of the LAST covered sampler index, so it is
    # still pure index arithmetic, clamped to the duration and the window end.
    for seg in report.segments:
        tail = last_covered.get(seg.frame_index, seg.frame_index)
        if tail != seg.frame_index:
            _, new_end = frame_bounds(tail, duration_ms=duration_ms, interval_s=interval_s)
            if end_ms is not None:
                new_end = min(new_end, end_ms)
            seg.end_ms = max(seg.end_ms, new_end)
    # Requirement: no stored chunk may claim content past the authoritative
    # duration. `frame_bounds` clamps, and this asserts it rather than trusting
    # it -- the strict-T gate is only as good as the upper bound it compares to.
    for seg in report.segments:
        if seg.end_ms > duration_ms:
            log.error(
                "visual: frame %d produced end_ms=%d beyond the %dms duration; "
                "clamping",
                seg.frame_index, seg.end_ms, duration_ms,
            )
            seg.end_ms = duration_ms
            seg.start_ms = min(seg.start_ms, max(duration_ms - 1, 0))
    n_calls = len(stats.latencies)
    report.timings = {
        "window_ms": [start_ms, end_ms],
        "sample_s": round(sample_s, 2),
        "vision_calls": list(stats.latencies),
        "bytes_per_call": list(stats.call_bytes),
        "frames_per_call": list(stats.call_frames),
        "retry_wait_s": round(gate.retry_wait_s, 2),
        "frames_sampled": len(sampled),
        "frames_sent": frames_sent,
        "skipped_duplicates": len(skipped),
        "raw_frame_bytes": raw_bytes,
        "sent_frame_bytes": sent_bytes,
        "calls": n_calls,
        "concurrency": concurrency,
        "total_s": round(time.monotonic() - t_start, 2),
    }
    log.info("visual: window timings %s", report.timings)
    if report.status != "complete":
        for warning in report.warnings:
            log.error("visual: %s", warning)
    return report


def extract_visual_segments(
    path: str,
    *,
    duration_ms: int,
    interval_s: float = FRAME_INTERVAL_S,
    batch_size: int = FRAMES_PER_BATCH,
    max_frames: int | None = None,
    describe: bool = True,
) -> list[VisualSegment]:
    """Segments only. Thin wrapper over :func:`extract_visual_pass`.

    Prefer the report form in the ingest pipeline -- it is the only way to see
    whether the pass covered the video. This wrapper exists for tests and for
    callers that genuinely only want the segments.
    """
    return extract_visual_pass(
        path,
        duration_ms=duration_ms,
        interval_s=interval_s,
        batch_size=batch_size,
        max_frames=max_frames,
        describe=describe,
    ).segments