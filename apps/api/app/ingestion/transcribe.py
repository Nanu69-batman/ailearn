"""Gemini transcription for audio/video files (no local whisper available).

Uploads the media via the Files API, prompts the model for timestamped JSON
segments, validates them, then deletes the remote file. Never prints or logs
any API key.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import logging
import math
import re
import struct
import time
from dataclasses import dataclass
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

# --- Duration bounds: ONE source of truth for prompt text and validation -----
#
# The prompt and the validators are built from these constants, so a rule can
# never again be tightened in the validator (or restated loosely in the prompt)
# without the other side following. Drift here is what previously made Gemini
# fail: the prompt's single "5-15 seconds" rule for both arrays was validated
# with a hardcoded 2-45s speech bound, while a written equation legitimately
# stays on screen far longer than any spoken sentence.
#
# Speech: a spoken sentence/beat. The 45s ceiling is deliberate slack (the
# prompt asks for 5-15s) because the chunker bounds windows independently;
# beyond that it is a mis-segment, not speech. Unchanged -- this protects
# transcript chunk quality.
MIN_SPEECH_SEGMENT_S = 2.0
MAX_SPEECH_SEGMENT_S = 45.0

# On-screen: written/displayed content. A worked derivation can stay on a board
# for minutes, so it gets its own, much larger ceiling. 120s is well above any
# plausible board sequence while still rejecting an element that has absorbed a
# whole lecture. The floor is lower than speech because a flashed label or a
# single-line heading is legitimately on screen for a moment.
MIN_VISUAL_SEGMENT_S = 1.0
MAX_VISUAL_SEGMENT_S = 120.0

# Figures: what is DRAWN/SHOWN -- colour, plotted curves, axes, arrows, gesture,
# diagram relationships. A drawn arrow is ink on the board, so it stays visible on
# the same timescale as writing: same floor, same ceiling, deliberately separate
# named constants so the figures prompt text and the figures validator cannot
# drift apart either (and so tuning one never silently retunes the other).
MIN_FIGURE_SEGMENT_S = 1.0
MAX_FIGURE_SEGMENT_S = 120.0

# --- Duration alignment: the anti-spoiler guard -------------------------------
#
# The strict-T gate (`end_ms <= T`) is the product's no-spoiler guarantee. It is
# only as good as the timestamps behind it. Gemini has been observed claiming
# ~1.5x the true duration of a 655.53s lecture (four independent runs, all
# overshooting), which silently invalidated T: a learner who had watched the
# whole video was refused, and conversely content could be revealed before it
# was actually said.
#
# Two rules, and only two:
#
# 1. Overshoot within this ratio is SELF-CORRECTED by scaling the time axis
#    DOWN onto the real duration. Direction matters and down is the only safe
#    direction: timestamps that are too large mean the gate under-delivers
#    (already-watched content shown early) which is merely unhelpful, whereas
#    timestamps that are too small leak the future, which is a spoiler. The
#    correction is a uniform multiplicative scale, so it is provably monotonic
#    and order-preserving: no segment can move later, and contiguity (the
#    validator's no-gap/no-overlap rule) is preserved exactly.
# 2. Overshoot beyond this ratio is a DRIFT, not rounding. Rescaling hides a
#    50% error behind a plausible-looking transcript, so the job FAILS instead
#    and nothing is indexed. Bad timestamps must never reach the store.
#
# 0.10 is deliberately tight. It absorbs prompt/rounding slack (a trailing
# segment may legitimately end a hair past the last sample) while being far
# below the observed 1.497x drift, so the real defect cannot slip through.
MAX_DURATION_OVERSHOOT_RATIO = 0.10

# --- Speech notation normalisation --------------------------------------------
#
# A maths/engineering lecturer speaks mathematics aloud as prose: "x squared y
# plus sine of y". Indexed verbatim, that prose barely matches a learner who
# types "x^2 y + sin(y)", while the clean written form exists only as scattered
# visual fragments -- so a question about the function retrieves nothing even
# though the material is right there.
#
# These rules make the MODEL re-spell only the mathematics, inline, keeping the
# instructor's own words for everything else. They are deliberately prompt-side
# and few-shot: a regex post-processor over ASR prose is a content-inventing
# machine on anything it did not anticipate (it cannot tell "x squared" the
# equation from "x squared off the top of my head" the aside). The rule and its
# examples are named constants substituted into the prompt by literal token
# replacement, so the instruction and the examples cannot drift apart -- the
# same one-source-of-truth pattern the duration bounds use below.
#
# Nothing here is validated in code, and that is correct: normalisation is a
# property of the model's output text, not of the segment contract. Timestamps,
# contiguity, coverage and the duration bounds are untouched by it.
SPEECH_NOTATION_RULE = (
    "Write any spoken mathematics inline in standard notation instead of as "
    "spelled-out prose, while keeping the instructor's own wording for "
    "everything else. Re-spell only the mathematics that was actually spoken: "
    "never add, infer or complete content that was not said, and keep "
    "hesitations and asides as the instructor uttered them. Notation only "
    "means digits, ^, / and parentheses -- no LaTeX, no markdown. "
    "Worked examples of this rule: "
)
SPEECH_NOTATION_EXAMPLES = (
    'x squared y plus sine of y -> "x^2 y + sin(y)"; '
    'the partial of f with respect to y -> "partial f / partial y"; '
    'd y d x -> "dy/dx"; '
    'one plus cosine of two -> "1 + cos(2)"; '
    'f of x comma y equals x squared y plus sine of y -> "f(x, y) = x^2 y + sin(y)"; '
    "an x -> \"x^2 y\" (the expression the instructor is completing)."
)

_PROMPT_TEMPLATE = (
    "Transcribe this {kind} titled {title!r} completely. "
    "Return ONLY a JSON object (no markdown fences, no commentary) with exactly "
    'three keys: "speech", "on_screen" and "figures". '
    '"speech" is a JSON array where each element is {{"start_s": <float seconds>, '
    '"end_s": <float seconds>, "text": "<spoken words>"}}. '
    '"on_screen" is a JSON array where each element is {{"start_s": <float seconds>, '
    '"end_s": <float seconds>, "on_screen_text": "<verbatim text that is written '
    'or displayed on screen>"}}. '
    '"figures" is a JSON array where each element is {{"start_s": <float seconds>, '
    '"end_s": <float seconds>, "figure_text": "<what is DRAWN or SHOWN on screen>"}}. '
    "Rules for speech: each element must be @MIN_SPEECH@-@MAX_SPEECH@ "
    "seconds long (aim for 5-15s), speech must start at 0, be contiguous and "
    "non-overlapping in ascending time order, and cover the FULL duration of "
    "the media with no gaps. Use plain text only. @SPEECH_NOTATION@"
    "@SPEECH_NOTATION_EXAMPLES@ Every element MUST have "
    "non-empty text: for silence, music, or other non-speech audio, write "
    'the text as "[non-speech]". '
    "For on_screen: include ONLY text that is actually written or displayed on "
    "screen (handwriting on a board or tablet, typed slides, headings, "
    "equations, graph or diagram labels). Transcribe it verbatim as it appears. "
    "Do NOT describe pictures, do NOT invent narration, and do NOT repeat the "
    "spoken transcript there. Each on_screen element must give the time range "
    "during which that writing is visible on screen, and may be @MIN_VISUAL@"
    "-@MAX_VISUAL@ seconds long -- unlike speech, an equation or a slide can "
    "stay visible far longer than a spoken sentence, so do NOT force it to "
    "match the speech length. on_screen elements need not cover the whole "
    "duration and need not be contiguous. Use an empty array [] when nothing "
    "is ever written on screen. "
    "For figures: describe ONLY what is genuinely VISIBLE -- the appearance and "
    "gesture of the board, not its text. Name the colour of each element "
    "(for example \"a yellow curve\", \"a blue arrow\"), what is plotted (curve, "
    "axis, arrow, box, highlight, sketch of a shape), and any drawing gesture "
    "(for example \"he draws an arrow from x towards y\", \"he shades the "
    "region under the curve\", \"he points at the highlighted box\"). Describe "
    "the relationship a diagram shows (what points to what, what is above or "
    "below what, which region is emphasized). Describe ONLY what you can actually "
    "see: if you cannot see an element clearly, omit that element entirely "
    "rather than guessing at it or inferring it from the speech. Do NOT invent a figure for a "
    "concept the instructor merely mentions. Do NOT put written text in "
    "figure_text -- verbatim writing belongs in on_screen, spoken words belong "
    "in speech, and any element already described in either of those must NOT be "
    "repeated in figures. Each figures element must give the time range during "
    "which that drawing or gesture is visible on screen, and may be "
    "@MIN_FIGURE@-@MAX_FIGURE@ seconds long. Split a long drawing sequence into "
    "SEPARATE elements as the drawing changes -- one element for the axes and "
    "curve, a later element for the arrows added on top of them -- so each "
    "element covers only the interval in which that particular thing is visible "
    "and is never left spanning a whole worked example. figures elements "
    "need not cover the "
    "whole duration and need not be contiguous. Use an empty array [] when "
    "nothing is ever drawn, pointed at or shown visually."
)

# Duration bounds are substituted by literal token replacement rather than
# ``str.format`` so that the ``{kind}``/``{title!r}`` placeholders -- filled in
# later, per media file -- survive untouched. The rendered prompt therefore
# always states the exact numbers both validators enforce.
TRANSCRIBE_PROMPT = (
    _PROMPT_TEMPLATE.replace("@MIN_SPEECH@", f"{MIN_SPEECH_SEGMENT_S:g}")
    .replace("@MAX_SPEECH@", f"{MAX_SPEECH_SEGMENT_S:g}")
    .replace("@MIN_VISUAL@", f"{MIN_VISUAL_SEGMENT_S:g}")
    .replace("@MAX_VISUAL@", f"{MAX_VISUAL_SEGMENT_S:g}")
    .replace("@MIN_FIGURE@", f"{MIN_FIGURE_SEGMENT_S:g}")
    .replace("@MAX_FIGURE@", f"{MAX_FIGURE_SEGMENT_S:g}")
    .replace("@SPEECH_NOTATION@", SPEECH_NOTATION_RULE)
    .replace("@SPEECH_NOTATION_EXAMPLES@", SPEECH_NOTATION_EXAMPLES)
)

_REPAIR_SUFFIX = (
    " Your previous response was rejected for this reason: {error}. "
    "Return the corrected full JSON object with the same three keys (same rules, "
    "non-empty text everywhere)."
)

# Cache key version. Bumped when the model response shape changes so cached
# audio-only transcripts (a bare speech array with no on-screen text) are never
# reused by the visual-aware path.
#
# v5: prompt-level notation normalisation (SPEECH_NOTATION_RULE /
# SPEECH_NOTATION_EXAMPLES) was added to TRANSCRIBE_PROMPT without a bump, so
# indexed snapshots still hold raw stuttery ASR ("that that dx"). Bumping
# forces re-transcription so the new prompt actually runs.
#
# v6: cached entries produced under v5 still carry the drifted timestamps
# (transcripts claiming 974.7s for a 655.5s clip). The chunker now bounds each
# window tightly, which only helps once fresh, better-aligned segment times are
# re-derived, so the cache is invalidated to force that re-transcription.
TRANSCRIPT_CACHE_VERSION = "v6"

# Similarity at or above which on-screen text is treated as a near-duplicate of
# what the instructor read aloud in the same window, and is dropped.
VISUAL_DEDUP_RATIO = 0.90

# Same threshold for the figures array, kept as its own named constant so tuning
# visual-text dedup can never silently change figure dedup (or vice versa).
FIGURE_DEDUP_RATIO = 0.90

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


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


@dataclass
class VisualSegment(Segment):
    """Text that is *written or displayed on screen* (board, tablet, slides).

    A `VisualSegment` is a `Segment` in every way that matters downstream --
    ``source_type == "video"`` and, critically, non-null ``start_ms``/``end_ms``
    so it flows through the same strict ``end_ms <= T`` retrieval gate as
    speech. A slide the instructor is currently pointing at is a spoiler, and
    that gate is the only thing preventing it, so this type never appears
    without both timestamps. The distinct class exists purely so the chunker
    can keep on-screen text in its own chunks instead of merging it into a
    30-60s speech window (which would smear it across speech timestamps).
    """

    __slots__ = ()

    # Namespaces the on-screen-text chunk ids. Deliberately NOT a source_type:
    # source_type must stay "video" for the strict-T gate to apply.
    chunk_kind = "visual"


class FigureSegment(VisualSegment):
    """What is *drawn or shown*: colour, curves, axes, arrows, gestures.

    A `FigureSegment` is a `VisualSegment` (and therefore a `Segment`): it
    subclasses it rather than copying it, so the chunker's existing
    ``isinstance(seg, VisualSegment)`` test picks figures up automatically and
    every safety property of visual content -- ``source_type == "video"``,
    non-null ``start_ms``/``end_ms``, one chunk per segment so the exact visible
    interval is preserved -- carries over unchanged.

    The reason this content must exist separately from speech and on-screen text
    is that a learner can only ask about what was *seen*: "what is that yellow
    function", "what does the arrow curve represent". The words colour, arrow and
    curve never appear in a transcript, so without this array those questions are
    unanswerable no matter how good the retrieval is.

    Subclassing carries the spoiler guarantee too: the strict ``end_ms <= T``
    gate in ``retrieval/store.py`` applies to ``source_type == "video"``, and a
    diagram the learner has not reached yet must stay a spoiler.
    """

    __slots__ = ()

    # Distinguishes the figure namespace from the on-screen-text namespace in
    # the chunk id hash. Deliberately NOT a source_type: source_type must stay
    # "video" or the strict-T gate would not apply.
    chunk_kind = "figure"


def _to_figure_segments(
    items: list[dict], index_start: int = 0
) -> list[FigureSegment]:
    """Convert validated figure items into FigureSegments.

    Identical rules to ``_to_visual_segments``: an item that cannot carry a real
    ``end_ms`` is DROPPED, never defaulted to null or zero, because a null
    ``end_ms`` is excluded by the defensive filter in ``retrieval/store.py`` --
    the correct outcome for content that cannot be proven finished by T.
    """
    out: list[FigureSegment] = []
    for offset, item in enumerate(items):
        try:
            text = str(item["figure_text"]).strip()
            start_ms = int(float(item["start_s"]) * 1000)
            end_ms = int(float(item["end_s"]) * 1000)
        except (KeyError, TypeError, ValueError):
            # No usable time range -> drop. Never default to 0/null.
            continue
        if not text:
            continue
        if start_ms < 0 or end_ms <= start_ms:
            continue
        out.append(
            FigureSegment(
                text=text,
                source_type="video",
                start_ms=start_ms,
                end_ms=end_ms,
                index=index_start + offset,
            )
        )
    return out


def _to_visual_segments(items: list[dict], index_start: int = 0) -> list[VisualSegment]:
    """Convert validated on-screen items into VisualSegments.

    Any item that cannot carry a real ``end_ms`` is DROPPED, never defaulted to
    null or zero. A null ``end_ms`` is excluded by the defensive filter in
    ``retrieval/store.py`` -- that is the correct outcome for content we cannot
    prove has finished by T.
    """
    out: list[VisualSegment] = []
    for offset, item in enumerate(items):
        text = str(item["on_screen_text"]).strip()
        if not text:
            continue
        start_ms = int(float(item["start_s"]) * 1000)
        end_ms = int(float(item["end_s"]) * 1000)
        if start_ms < 0 or end_ms <= start_ms:
            continue
        out.append(
            VisualSegment(
                text=text,
                source_type="video",
                start_ms=start_ms,
                end_ms=end_ms,
                index=index_start + offset,
            )
        )
    return out
_DOC_MIMES = {
    ".pdf": "application/pdf",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".ppt": "application/vnd.ms-powerpoint",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
}


def media_mime_type(path: str | Path) -> str:
    """Detect a mime type from the suffix (video, audio, or document)."""
    suffix = Path(path).suffix.lower()
    if suffix in _VIDEO_MIMES:
        return _VIDEO_MIMES[suffix]
    if suffix in _AUDIO_MIMES:
        return _AUDIO_MIMES[suffix]
    if suffix in _DOC_MIMES:
        return _DOC_MIMES[suffix]
    return "video/mp4" if suffix in (".avi", ".m4v") else "application/octet-stream"


def _is_audio_path(path: str | Path) -> bool:
    return Path(path).suffix.lower() in _AUDIO_MIMES


# --- Media duration probing (dependency-free) ---------------------------------
#
# Duration MUST be known before transcription, because every duration guard in
# this module is conditioned on it. When it is None the guards are silently
# skipped, which is precisely how a 1.5x-drifted transcript reached the store.
#
# ffprobe/ffmpeg are not guaranteed to be installed, and no pure-Python media
# library is in requirements.txt, so this reads the ISO-BMFF (MP4/MOV) box tree
# directly. No new dependency: the format is stable and the needed fields are
# few.
#
# Two shapes must be handled:
#   * "regular" MP4  -> moov/mvhd carries the whole duration. Fast path.
#   * "fragmented" MP4 (mvex + moof, what MediaRecorder and most muxers emit)
#     -> mvhd/mdhd durations are written as 0, so duration must be recovered
#     from the fragments. Per track it is:
#         last tfdt baseMediaDecodeTime + (samples in the last traf x that
#         fragment's sample duration)
#     using the *last* traf's own durations. Summing per-track totals instead is
#     wrong: tfhd's default_sample_duration may legitimately differ between
#     fragments of the same track (audio here carries both 1024 and 2494), so a
#     single track-wide default inflates the audio track by ~2.4x. Taking the max
#     over tracks then lands on the true presentation length.
_CONTAINER_BOXES = frozenset(
    {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"edts", b"mvex", b"moof", b"traf"}
)
_MP4_SUFFIXES = frozenset({".mp4", ".mov", ".m4v", ".m4a", ".aac", ".3gp"})


def _iter_boxes(buf: bytes, start: int, end: int):
    """Yield (offset, header_size, size, type) for the boxes in [start, end)."""
    off = start
    while off + 8 <= end:
        size = struct.unpack_from(">I", buf, off)[0]
        typ = buf[off + 4:off + 8]
        hdr = 8
        if size == 1:
            size = struct.unpack_from(">Q", buf, off + 8)[0]
            hdr = 16
        elif size == 0:
            size = end - off
        if size < hdr or off + size > end:
            return
        yield off, hdr, size, typ
        if typ in _CONTAINER_BOXES:
            yield from _iter_boxes(buf, off + hdr, off + size)
        off += size


def _full_box_children(buf: bytes, start: int, end: int, want: bytes):
    """Yield (payload_start, payload_end) for every occurrence of `want`.

    ``_iter_boxes`` already flattens container boxes, so this is a plain filter
    -- recursing again here would visit every descendant once per ancestor.
    """
    for off, hdr, size, typ in _iter_boxes(buf, start, end):
        if typ == want:
            yield off + hdr, off + size


def _track_timescales(buf: bytes, start: int, end: int) -> dict[int, int]:
    """Map track_id -> media timescale by pairing each trak's tkhd and mdhd."""
    result: dict[int, int] = {}
    for off, hdr, size, typ in _iter_boxes(buf, start, end):
        if typ != b"trak":
            continue
        track_id: int | None = None
        timescale = 0
        for s, e in _full_box_children(buf, off + hdr, off + size, b"tkhd"):
            if e - s >= 20:
                ver = buf[s]
                track_id = struct.unpack_from(
                    ">I", buf, s + (20 if ver == 1 else 12)
                )[0]
            break
        for s, e in _full_box_children(buf, off + hdr, off + size, b"mdhd"):
            ts, _dur = _timescale_and_duration(buf, s, e)
            timescale = ts or 0
            break
        if track_id and timescale:
            result[track_id] = timescale
    return result


def _timescale_and_duration(buf: bytes, payload_start: int, payload_end: int):
    """Decode a (timescale, duration) pair from an mvhd/mdhd box payload.

    Layout after the 4-byte version+flags: creation_time and modification_time
    come first (4 bytes each for version 0, 8 each for version 1), then
    timescale and duration. tfdt has no such fields, which is why its offsets
    differ and why this helper is not reused there.
    """
    if payload_end - payload_start < 4:
        return None, None
    version = buf[payload_start]
    try:
        if version == 1:
            if payload_end - payload_start < 32:
                return None, None
            timescale = struct.unpack_from(">I", buf, payload_start + 20)[0]
            duration = struct.unpack_from(">Q", buf, payload_start + 24)[0]
        else:
            if payload_end - payload_start < 20:
                return None, None
            timescale = struct.unpack_from(">I", buf, payload_start + 12)[0]
            duration = struct.unpack_from(">I", buf, payload_start + 16)[0]
    except struct.error:
        return None, None
    return timescale, duration


def _trun_sample_durations(buf: bytes, start: int, end: int, default: int | None):
    """Sum the sample durations across every trun in [start, end).

    Returns (total, all_explicit). When `all_explicit` is False the caller
    supplied `default` was used for the samples that carried no explicit
    duration, and the returned total is only valid alongside that default.
    """
    total = 0
    all_explicit = True
    for off, hdr, size, typ in _iter_boxes(buf, start, end):
        if typ != b"trun":
            continue
        p = off + hdr
        if p + 8 > off + size:
            continue
        flags = int.from_bytes(buf[p + 1:p + 4], "big")
        count = struct.unpack_from(">I", buf, p + 4)[0]
        per_sample = 4 * sum(
            1 for m in (0x100, 0x200, 0x400, 0x800) if flags & m
        )
        cursor = p + 8
        end_box = off + size
        for _ in range(count):
            if cursor + per_sample > end_box:
                return total, False
            if flags & 0x100:
                total += struct.unpack_from(">I", buf, cursor)[0]
            elif default is not None:
                total += default
                all_explicit = False
            else:
                all_explicit = False
            cursor += per_sample
    return total, all_explicit


def _fragmented_track_seconds(buf: bytes, start: int, end: int) -> list[float]:
    """Recover each fragmented track's duration in seconds.

    Fragments from different tracks interleave in file order (video, audio,
    video, audio...), so a traf is mapped to its timescale through the track_id
    in its tfhd -- never by counting trafs, which would read the video track's
    timescale for an audio fragment and vice versa.
    """
    timescales = _track_timescales(buf, start, end)
    if not timescales:
        return []

    # Per track: the latest decode time seen, and the tail duration of the most
    # recent fragment. Duration is baseMediaDecodeTime + that fragment's samples.
    base_times: dict[int, int] = {}
    tails: dict[int, int] = {}
    for off, hdr, size, typ in _iter_boxes(buf, start, end):
        if typ != b"traf":
            continue
        traf_start, traf_end = off + hdr, off + size
        track_id: int | None = None
        default: int | None = None
        for s, e in _full_box_children(buf, traf_start, traf_end, b"tfhd"):
            if e - s < 8:
                continue
            flags = int.from_bytes(buf[s + 1:s + 4], "big")
            track_id = struct.unpack_from(">I", buf, s + 4)[0]
            cursor = s + 8
            if flags & 0x1:
                cursor += 8
            if flags & 0x2:
                cursor += 4
            if flags & 0x8 and cursor + 4 <= e:
                default = struct.unpack_from(">I", buf, cursor)[0]
            break
        if track_id is None or track_id not in timescales:
            continue

        base_time = base_times.get(track_id, 0)
        for s, e in _full_box_children(buf, traf_start, traf_end, b"tfdt"):
            if e - s >= 12 and buf[s] == 1:
                base_time = max(base_time, struct.unpack_from(">Q", buf, s + 4)[0])
            elif e - s >= 8:
                base_time = max(base_time, struct.unpack_from(">I", buf, s + 4)[0])

        tail, _explicit = _trun_sample_durations(buf, traf_start, traf_end, default)
        base_times[track_id] = base_time
        tails[track_id] = tail

    return [
        (base_times[tid] + tails[tid]) / timescales[tid]
        for tid in timescales
        if tid in base_times and (base_times[tid] or tails[tid])
    ]


def probe_media_duration_s(path: str | Path) -> float | None:
    """Measure a media file's real duration in seconds, or None if unknowable.

    Returns None (never a guess) for unreadable files, non-MP4 containers, and
    MP4s whose duration cannot be recovered. Callers MUST treat None as "the
    duration guards cannot run" and say so loudly rather than proceeding as if
    the transcript had been checked against the media.
    """
    target = Path(path)
    if target.suffix.lower() not in _MP4_SUFFIXES:
        return None
    try:
        buf = target.read_bytes()
    except OSError as exc:
        log.warning("cannot read %s to measure duration: %s", target.name, exc)
        return None
    if len(buf) < 8 or buf[4:8] not in (b"ftyp", b"moov", b"styp", b"moof", b"mdat"):
        return None

    for s, e in _full_box_children(buf, 0, len(buf), b"mvhd"):
        timescale, duration = _timescale_and_duration(buf, s, e)
        if timescale and duration:
            return duration / timescale

    seconds = _fragmented_track_seconds(buf, 0, len(buf))
    if not seconds:
        return None
    # Longest track: audio and video can differ by a frame, and the gate must
    # not be stricter than the media actually is.
    return max(seconds)


def clamp_segments_to_duration(
    segments: list[dict[str, Any]], duration_s: float
) -> list[dict[str, Any]]:
    """Scale a transcript's time axis DOWN so it fits inside `duration_s`.

    SAFETY DIRECTION: every timestamp can only move EARLIER. Overshoot means
    the timestamps are too large, and too-large timestamps under-gate (the
    learner is shown material they have already watched), whereas too-small
    timestamps expose the future. Shrinking is therefore the conservative
    correction; this function can never increase a value.

    Monotonicity: a uniform positive scale factor, so relative order and
    start<end within every segment are both preserved, and adjacent segments
    stay exactly as contiguous (or as gapped) as they were.
    """
    if not segments or duration_s <= 0:
        return segments
    declared_end = max(float(seg["end_s"]) for seg in segments)
    if declared_end <= duration_s:
        return segments
    factor = duration_s / declared_end
    clamped: list[dict[str, Any]] = []
    for seg in segments:
        new_start = float(seg["start_s"]) * factor
        new_end = float(seg["end_s"]) * factor
        # factor < 1 here, so these clamps are belt-and-braces: they assert the
        # invariant the safety argument depends on rather than establish it.
        clamped.append(
            {
                **seg,
                "start_s": min(new_start, duration_s),
                "end_s": min(new_end, duration_s),
            }
        )
    log.warning(
        "transcript overshoot corrected: declared %.1fs vs media %.1fs "
        "(x%.3f); timestamps scaled DOWN by %.3f",
        declared_end,
        duration_s,
        declared_end / duration_s,
        factor,
    )
    return clamped


def _declared_end_s(data: list[dict[str, Any]]) -> float | None:
    """Largest end_s in the payload, or None if any value is unusable.

    Alignment runs BEFORE the per-segment validator, so it must not be the
    thing that crashes on a malformed segment -- the validator owns that, with
    far better error messages.
    """
    ends: list[float] = []
    for seg in data:
        try:
            ends.append(float(seg["end_s"]))
        except (KeyError, TypeError, ValueError):
            return None
    return max(ends) if ends else None


def enforce_duration_alignment(
    data: list[dict[str, Any]], duration_s: float | None
) -> list[dict[str, Any]]:
    """Reject or correct a transcript that disagrees with the media duration.

    Returns the (possibly clamped) segments. Raises ValueError when the
    overshoot exceeds MAX_DURATION_OVERSHOOT_RATIO. When `duration_s` is None
    the guard cannot run: that is logged as a warning, because a caller that
    cannot measure its media is the exact condition that let a 1.5x-drifted
    transcript be indexed unnoticed.
    """
    if duration_s is None:
        log.warning(
            "duration guard inactive: media duration unknown, transcript "
            "timestamps will NOT be validated against the media"
        )
        return data
    declared_end = _declared_end_s(data)
    if declared_end is None or declared_end <= 0:
        # Malformed payload: let the per-segment validator produce the error.
        return data
    ratio = declared_end / duration_s
    if ratio > 1.0 + MAX_DURATION_OVERSHOOT_RATIO:
        raise ValueError(
            f"transcript claims {declared_end:.1f}s but the media is only "
            f"{duration_s:.1f}s ({ratio:.2f}x, over the "
            f"{MAX_DURATION_OVERSHOOT_RATIO:.0%} tolerance); refusing to index "
            f"timestamps that do not match the media"
        )
    return clamp_segments_to_duration(data, duration_s)


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
    # Alignment runs first, on the raw payload, so that a drifted transcript is
    # rejected (or corrected downwards) BEFORE the per-segment bounds below
    # reject it for the wrong reason. Doing it here rather than at the call
    # sites means no path -- live response or cache replay -- can bypass it.
    data = enforce_duration_alignment(data, duration_s)
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
        # Bounds come from the same constants the prompt is built from, so the
        # two cannot drift apart. The prompt asks for 5-15s; the slack up to
        # MAX_SPEECH_SEGMENT_S is because the chunker bounds windows
        # independently. Beyond that it is garbage, not speech.
        if length < MIN_SPEECH_SEGMENT_S or length > MAX_SPEECH_SEGMENT_S:
            raise ValueError(
                f"segment {i} length {length:.1f}s outside "
                f"{MIN_SPEECH_SEGMENT_S:g}-{MAX_SPEECH_SEGMENT_S:g}s"
            )
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
    """Strip fences, JSON-parse, and validate a model response.

    Accepts the legacy bare speech array as well as the ``{"speech": [...],
    "on_screen": [...]}`` object, and returns only the speech segments (the
    historical return value, unchanged).
    """
    speech, _visual, _figures = parse_transcribe_json(raw, duration_s)
    return speech


def strip_json_fences(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = [ln for ln in lines if not ln.strip().startswith("```")]
        text = "\n".join(lines).strip()
    return text


def _validate_timed_text_array(
    data: Any,
    duration_s: float | None,
    text_key: str,
    min_len_s: float,
    max_len_s: float,
) -> list[dict[str, Any]]:
    """Shared validator for the on_screen and figures arrays.

    Both are BEST-EFFORT by design: a malformed element is dropped rather than
    failing the whole transcription. The speech array is validated strictly (it
    is the contract); visual content is a bonus that must never break ingestion.
    The one thing never tolerated is a missing ``end_s`` -- an element without a
    real end time cannot prove it finished before T, so it is dropped instead of
    being emitted with a null/zero end.

    The length bounds are passed in by the caller from the SAME named constants
    the prompt text is rendered from, so prompt and validator cannot drift for
    either array.
    """
    if data is None:
        return []
    if not isinstance(data, list):
        raise TypeError(f"{text_key} payload must be a JSON array")
    cleaned: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            start = float(item["start_s"])
            end = float(item["end_s"])
            text = str(item[text_key]).strip()
        except (KeyError, TypeError, ValueError):
            # No usable time range -> drop. Never default to 0/null.
            continue
        if not text or not math.isfinite(start) or not math.isfinite(end):
            continue
        if start < 0 or end <= start:
            continue
        length = end - start
        if length < min_len_s or length > max_len_s:
            continue
        if duration_s is not None and end > duration_s + 2.0:
            continue
        cleaned.append({"start_s": start, "end_s": end, text_key: text})
    cleaned.sort(key=lambda d: (d["start_s"], d["end_s"]))
    # On-screen elements need not be contiguous (slides come and go), but they
    # must not overlap: an overlapping pair means the model lost track of when
    # each piece of writing was on screen. Dropped, not fatal.
    deduped: list[dict[str, Any]] = []
    last_end = float("-inf")
    for item in cleaned:
        if item["start_s"] < last_end:
            continue
        deduped.append(item)
        last_end = item["end_s"]
    return deduped


def validate_visual_payload(
    data: Any, duration_s: float | None = None
) -> list[dict[str, Any]]:
    """Validate the on-screen array into [{start_s, end_s, on_screen_text}].

    Bounds come from the SAME named constants the prompt text is rendered from,
    so the two cannot drift.

    Raises TypeError when the payload is present but is not a list.
    """
    return _validate_timed_text_array(
        data,
        duration_s,
        "on_screen_text",
        MIN_VISUAL_SEGMENT_S,
        MAX_VISUAL_SEGMENT_S,
    )


def validate_figure_payload(
    data: Any, duration_s: float | None = None
) -> list[dict[str, Any]]:
    """Validate the figures array into [{start_s, end_s, figure_text}].

    Same best-effort contract as ``validate_visual_payload`` and bounded by the
    same shared-constant pattern (``MIN/MAX_FIGURE_SEGMENT_S``, the constants the
    prompt text is rendered from), so prompt and validator cannot drift for the
    figures array either.

    Raises TypeError when the payload is present but is not a list.
    """
    return _validate_timed_text_array(
        data,
        duration_s,
        "figure_text",
        MIN_FIGURE_SEGMENT_S,
        MAX_FIGURE_SEGMENT_S,
    )


def parse_transcribe_json(
    raw: str, duration_s: float | None = None
) -> tuple[list[dict], list[dict], list[dict]]:
    """Parse a model response into (speech_items, visual_items, figure_items).

    Speech keeps the strict validation of ``validate_transcript_payload``; the
    on-screen and figures arrays are best-effort. A legacy bare speech array is
    still accepted (both visual arrays come back empty) so an older cached
    response replays.
    """
    try:
        data = json.loads(strip_json_fences(raw))
    except json.JSONDecodeError as exc:
        raise ValueError(f"transcript response is not valid JSON: {exc}") from exc
    if isinstance(data, dict):
        speech = validate_transcript_payload(data.get("speech"), duration_s)
        visual = validate_visual_payload(data.get("on_screen"), duration_s)
        figures = validate_figure_payload(data.get("figures"), duration_s)
        # Order matters: figures are deduped against the *surviving* on-screen
        # text, so a figure that merely restates writing already dropped as
        # read-aloud speech is also dropped here.
        visual = dedupe_visual_against_speech(speech, visual)
        figures = dedupe_figures_against(speech, visual, figures)
        return speech, visual, figures
    return validate_transcript_payload(data, duration_s), [], []


def _normalize_for_dedup(text: str) -> str:
    return _NON_ALNUM.sub(" ", text.lower()).strip()


def _texts_overlap(a_start: float, a_end: float, b_start: float, b_end: float) -> bool:
    """True when two timed items share a window."""
    return not (a_end < b_start or a_start > b_end)


def _is_near_duplicate(left: str, right: str, ratio: float) -> bool:
    """Exact / contained / near-exact match between two normalized strings."""
    if left == right:
        return True
    if left in right or right in left:
        return True
    return difflib.SequenceMatcher(None, left, right).ratio() >= ratio


def dedupe_visual_against_speech(
    speech: list[dict], visual: list[dict]
) -> list[dict]:
    """Drop on-screen text the instructor also read aloud in the same window.

    The instructor commonly narrates what he is writing ("this is x squared,
    plus sine of y") immediately while writing it. Indexing both copies
    double-weights that text in the embedding space and skews top-k, so an
    on-screen segment whose normalized text matches (or is a near-duplicate of)
    the speech overlapping it is dropped. Near-exact is a similarity ratio
    rather than string equality because ASR renders an equation differently
    from the handwriting it transcribes.
    """
    kept: list[dict] = []
    for vis in visual:
        vnorm = _normalize_for_dedup(vis["on_screen_text"])
        if not vnorm:
            continue
        vstart, vend = vis["start_s"], vis["end_s"]
        duplicate = False
        for seg in speech:
            # Same window only: an equation written at 30s is not covered by
            # speech that read it out at 600s.
            if seg["end_s"] < vstart or seg["start_s"] > vend:
                continue
            snorm = _normalize_for_dedup(seg["text"])
            if not snorm:
                continue
            if _is_near_duplicate(snorm, vnorm, VISUAL_DEDUP_RATIO):
                duplicate = True
                break
        if not duplicate:
            kept.append(vis)
    return kept


def dedupe_figures_against(
    speech: list[dict], visual: list[dict], figures: list[dict]
) -> list[dict]:
    """Drop figures already indexed as speech or as on-screen text.

    A figure is only additive when it carries information the other two arrays
    do not. "He draws an arrow" said aloud while drawing it is the same content
    twice, and a figure entry that merely restates the writing on the board adds
    nothing the on_screen entry does not already answer. Both comparisons are
    SAME-WINDOW only: a curve drawn at 30s must not be considered narrated by
    speech at 600s, because the speech copy cannot serve a learner paused at the
    moment the curve appeared.

    ``visual`` should be the already-deduped on-screen list so a figure cannot
    resurrect a caption that was itself dropped as read-aloud.
    """
    # Both reference arrays are normalized into (text, start, end) triples once,
    # rather than re-normalized per candidate.
    speech_refs = [
        (_normalize_for_dedup(s["text"]), s["start_s"], s["end_s"]) for s in speech
    ]
    visual_refs = [
        (_normalize_for_dedup(v["on_screen_text"]), v["start_s"], v["end_s"])
        for v in visual
    ]
    refs = [r for r in (*speech_refs, *visual_refs) if r[0]]

    kept: list[dict] = []
    for fig in figures:
        fnorm = _normalize_for_dedup(fig["figure_text"])
        if not fnorm:
            continue
        fstart, fend = fig["start_s"], fig["end_s"]
        if any(
            _texts_overlap(fstart, fend, rstart, rend)
            and _is_near_duplicate(rnorm, fnorm, FIGURE_DEDUP_RATIO)
            for rnorm, rstart, rend in refs
        ):
            continue
        kept.append(fig)
    return kept


def transcribe_with_gemini(
    media_path: str | Path, title: str, duration_s: float | None = None
) -> list[Segment]:
    """Transcribe a local media file with Gemini; returns timestamped Segments.

    The returned list is speech segments first (byte-compatible with the
    audio-only behaviour: same fields, same order, same ``index`` values),
    followed by the on-screen written-content segments. Callers that only care
    about speech must not assume length or contiguity -- use
    ``transcribe_speech_visual_figures`` when the three need separating.
    """
    speech, visual, figures = transcribe_speech_visual_figures(
        media_path, title, duration_s
    )
    # `transcribe_speech_visual_figures` already returns Segment objects (it
    # converts internally on both the live and the cache-replay path).
    # Re-wrapping them here passed Segment objects to _to_segments, which
    # subscripts dicts, and failed every cache-hit ingest with
    # "'Segment' object is not subscriptable". Concatenate as-is.
    segments = list(speech) + list(visual) + list(figures)
    log.info(
        "transcribed %s into %d speech + %d visual + %d figure segments",
        title,
        len(speech),
        len(visual),
        len(figures),
    )
    return segments


def transcribe_speech_and_visual(
    media_path: str | Path, title: str, duration_s: float | None = None
) -> tuple[list[Segment], list[VisualSegment]]:
    """Transcribe a local media file; returns (speech, on-screen) Segments.

    Kept as the two-tuple view for callers that predate figures.
    """
    speech, visual, _figures = transcribe_speech_visual_figures(
        media_path, title, duration_s
    )
    return speech, visual


def transcribe_speech_visual_figures(
    media_path: str | Path, title: str, duration_s: float | None = None
) -> tuple[list[Segment], list[VisualSegment], list[FigureSegment]]:
    """Transcribe a local media file.

    Returns (speech, on-screen, figures) Segments. The two visual lists are
    FigureSegment/VisualSegment instances, so every chunker safety property --
    ``source_type == "video"``, non-null ``start_ms``/``end_ms`` -- applies
    unchanged and each element keeps its own exact visible interval.
    """
    api_key = settings.llm_api_key or settings.embedding_api_key
    if not api_key:
        raise RuntimeError("LLM_API_KEY not set; transcribe_with_gemini unavailable")
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise RuntimeError("google-genai package not installed") from exc

    path = Path(media_path)
    cache_key = transcript_cache_key(path)
    if cache_enabled():
        cached = get_cache("transcripts").get(cache_key)
        if cached is not None:
            log.info("transcript cache hit for %s", path.name)
            speech_items, visual_items, figure_items = _replay_cached(cached, duration_s)
            return (
                _to_segments(speech_items),
                _to_visual_segments(visual_items, len(speech_items)),
                _to_figure_segments(figure_items, len(speech_items) + len(visual_items)),
            )
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
        speech_items: list[dict] | None = None
        visual_items: list[dict] = []
        figure_items: list[dict] = []
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
                speech_items, visual_items, figure_items = parse_transcribe_json(
                    resp.text or "", duration_s
                )
                break
            except (ValueError, TypeError) as exc:
                log.warning("transcript validation failed: %s; retrying", exc)
                last_error = _REPAIR_SUFFIX.format(error=exc)
        if speech_items is None:
            raise ValueError(f"transcript failed validation twice: {last_error}")
        if cache_enabled():
            get_cache("transcripts").set(
                cache_key,
                {
                    "speech": speech_items,
                    "on_screen": visual_items,
                    "figures": figure_items,
                },
            )
    finally:
        try:
            client.files.delete(name=remote.name)
        except Exception as exc:  # noqa: BLE001 - cleanup best-effort
            log.warning("remote file cleanup failed: %s", type(exc).__name__)
    return (
        _to_segments(speech_items),
        _to_visual_segments(visual_items, len(speech_items)),
        _to_figure_segments(figure_items, len(speech_items) + len(visual_items)),
    )


def transcript_cache_key(path: str | Path) -> str:
    """Disk-cache key for a transcription.

    ``TRANSCRIPT_CACHE_VERSION`` is part of the key: the response shape changed
    from a bare speech array to an object carrying on-screen text, then to one
    also carrying figure/diagram narration, so an entry written by an older build
    must not be replayed as if it had been checked for the content it lacks.
    """
    return (
        "transcript:"
        + TRANSCRIPT_CACHE_VERSION
        + ":"
        + settings.llm_model
        + ":"
        + _file_sha256(Path(path))
    )


def _replay_cached(
    cached: Any, duration_s: float | None
) -> tuple[list[dict], list[dict], list[dict]]:
    """Read a cached transcript, tolerating the v1, v2 and v3 entry shapes.

    Dedup is NOT re-applied here: the cache stores already-deduped arrays.
    """
    if isinstance(cached, dict):
        return (
            validate_transcript_payload(cached.get("speech"), duration_s),
            validate_visual_payload(cached.get("on_screen"), duration_s),
            validate_figure_payload(cached.get("figures"), duration_s),
        )
    return validate_transcript_payload(cached, duration_s), [], []
