"""Authoritative media duration, read from the container header.

Why this module exists at all
-----------------------------
The strict-T gate compares ``end_ms <= T`` (ADR-002 §5.1). If the duration we
believe the media to have is *wrong*, the gate is comparing against a fiction
and the spoiler rate goes back up. So duration is a safety input, not a
display nicety, and it is resolved in a fixed order of decreasing authority:

1. ``mvhd`` / ``mehd`` box in the ISO-BMFF container -- authoritative, no
   decode, microseconds of work even for a multi-GB file.
2. PyAV container duration (ships with faster-whisper) -- used when the header
   is degenerate (zero/absent timescale, truncated ``moov``).
3. ``None``, **logged at ERROR level**.

Step 3 is a design commitment, not a fallback of convenience. A silently
unavailable duration guard is exactly the failure mode ADR-002 warns about: the
pipeline "succeeds" while the property that matters is unenforced. Callers
treat ``None`` as a hard stop.

Box offset arithmetic (the bug this module exists to prevent)
-------------------------------------------------------------
An earlier implementation read the ``mvhd`` payload at ``+12``/``+16`` and got
a ``timescale: 0``. The offsets below are relative to **the start of the box's
4-byte type field**, i.e. 4 bytes after the 8-byte box header::

    box start
    +0   uint32  size
    +4   char[4] type                <- "relative origin" for every offset below
    +4   uint8   version             <- version+flags, 4 bytes
    +8   version==0: uint32 creation_time
    +12  version==0: uint32 modification_time
    +16  version==0: uint32 timescale      <-- v0
    +20  version==0: uint32 duration       <-- v0
    +8   version==1: uint64 creation_time
    +16  version==1: uint64 modification_time
    +24  version==1: uint32 timescale      <-- v1
    +28  version==1: uint64 duration       <-- v1 (8 bytes wide!)

Reading v1 duration as 4 bytes, or reading v0 at the v1 offsets, silently
yields a wrong or absurd duration. That is why ``test_duration_matches_known_
media`` pins the real 655.5s of ``test-data/Partial-derivatives.mp4``.
"""

from __future__ import annotations

import logging
import struct
from pathlib import Path

log = logging.getLogger(__name__)

# Absolute offsets from the start of the TYPE FIELD, per the ISO-BMFF spec and
# the arithmetic table in the module docstring. ``_VERSION_OFF`` is the
# version+flags word; the timebase fields begin 8 bytes later.
_VERSION_OFF = 4
_TIMEBASE_OFF = 8  # start of creation_time
_V0_TIMESCALE_OFF = 16
_V0_DURATION_OFF = 20
_V1_TIMESCALE_OFF = 24
_V1_DURATION_OFF = 28
#: ``mehd`` has no creation/modification fields: version+flags then the
#: fragment duration, which is expressed in the movie timescale (``mvhd``).
_MEHD_FRAGMENT_DURATION_OFF = 8

#: Refuse a header-derived duration below this many milliseconds. Below 1ms a
#: timescale/duration pair is a parse error, not a very short video.
MIN_PLAUSIBLE_DURATION_MS = 1

#: Boxes we descend into while walking the container. ``mvhd`` is a direct
#: child of ``moov``; ``mehd`` lives under ``moof``/``traf`` in fragmented MP4.
_MOOV_CHILDREN = (b"trak", b"edts", b"mdia", b"minf", b"stbl")
_TRAF_CHILDREN = (b"tfhd", b"tfdt", b"trun")


def _iter_boxes(buf: bytes, start: int, end: int):
    """Yield ``(type, origin, body_start, box_end)`` for boxes in ``buf[start:end]``.

    ``origin`` is the offset of the box's 4-byte TYPE field -- 4 bytes past the
    size field. That is the relative origin for every offset constant in this
    module, which is why ``version`` lives at ``origin + 4`` rather than at
    ``origin``. Getting this wrong shifts every field by 4 bytes and yields a
    plausible-looking but wrong timescale (it did: ``duration: 100ms``).

    ``body_start`` is where this box's CHILDREN begin. Recursion must walk from
    there, never from ``origin - 4``: feeding the parent's own start position
    back into this generator re-yields the parent forever (it also did).
    """
    pos = start
    while pos + 8 <= end:
        (size,) = struct.unpack_from(">I", buf, pos)
        btype = buf[pos + 4 : pos + 8]
        header = 8
        if size == 1:
            if pos + 16 > end:
                return
            (size,) = struct.unpack_from(">Q", buf, pos + 8)
            header = 16
        elif size == 0:
            # "box extends to end of file" -- legal, rare.
            size = end - pos
        if size < header or pos + size > end:
            return
        yield btype, pos + 4, pos + header, pos + size
        pos += size


def _full_box_version(origin: int, buf: bytes, box_end: int) -> int:
    """Version byte of a FullBox: origin(type field) + 4."""
    if origin + 5 > box_end:
        return -1
    return buf[origin + 4]


def _read_mvhd(buf: bytes) -> float | None:
    """Parse the first ``mvhd`` box found under any ``moov`` in ``buf``."""
    for btype, _o, body, be in _iter_boxes(buf, 0, len(buf)):
        if btype != b"moov":
            continue
        stack = [(body, be)]
        while stack:
            lo, hi = stack.pop()
            for ctype, cps, cbody, cbe in _iter_boxes(buf, lo, hi):
                if ctype == b"mvhd":
                    return _parse_mvhd(buf, cps, cbe)
                if ctype in _MOOV_CHILDREN:
                    stack.append((cbody, cbe))
    return None


def _parse_mvhd(buf: bytes, payload_start: int, box_end: int) -> float | None:
    version = _full_box_version(payload_start, buf, box_end)
    if version == 0:
        ts_off = payload_start + _V0_TIMESCALE_OFF
        dur_off = payload_start + _V0_DURATION_OFF
        if box_end < dur_off + 4:
            return None
        timescale, = struct.unpack_from(">I", buf, ts_off)
        duration, = struct.unpack_from(">I", buf, dur_off)
    elif version == 1:
        ts_off = payload_start + _V1_TIMESCALE_OFF
        dur_off = payload_start + _V1_DURATION_OFF
        if box_end < dur_off + 8:
            return None
        timescale, = struct.unpack_from(">I", buf, ts_off)
        duration, = struct.unpack_from(">Q", buf, dur_off)
    else:
        return None
    if timescale <= 0 or duration <= 0:
        return None
    return duration / timescale


def _movie_timescale(buf: bytes) -> int | None:
    """The ``mvhd`` movie timescale, needed to scale any ``mehd`` fragment."""
    for btype, _o, body, be in _iter_boxes(buf, 0, len(buf)):
        if btype != b"moov":
            continue
        for ctype, cps, _cb, cbe in _iter_boxes(buf, body, be):
            if ctype != b"mvhd":
                continue
            version = _full_box_version(cps, buf, cbe)
            off = cps + (_V0_TIMESCALE_OFF if version == 0 else _V1_TIMESCALE_OFF)
            if cbe < off + 4:
                return None
            (ts,) = struct.unpack_from(">I", buf, off)
            return ts if ts > 0 else None
    return None


def _read_mehd(buf: bytes) -> float | None:
    """Parse ``mehd`` from a fragmented MP4 (fragment-duration in timescale units).

    Only used when ``mvhd`` is unusable, which in practice means the ``moov``
    was truncated or the file is still being written.
    """
    for btype, _o, body, be in _iter_boxes(buf, 0, len(buf)):
        if btype != b"moof":
            continue
        stack = [(body, be)]
        while stack:
            lo, hi = stack.pop()
            for ctype, cps, cbody, cbe in _iter_boxes(buf, lo, hi):
                if ctype == b"traf":
                    stack.append((cbody, cbe))
                elif ctype == b"mehd":
                    # mehd is a sibling of traf under moof, not a child of it.
                    # mehd carries ONLY the fragment duration, in the movie
                    # timescale from mvhd -- so without a usable mvhd timescale
                    # there is nothing to divide by. Returning None here is the
                    # correct answer, not a shortcut.
                    version = _full_box_version(cps, buf, cbe)
                    dur_off = cps + _MEHD_FRAGMENT_DURATION_OFF
                    timescale = _movie_timescale(buf)
                    if timescale is None or timescale <= 0:
                        continue
                    if version == 1 and cbe >= dur_off + 8:
                        duration, = struct.unpack_from(">Q", buf, dur_off)
                    elif version == 0 and cbe >= dur_off + 4:
                        duration, = struct.unpack_from(">I", buf, dur_off)
                    else:
                        continue
                    if duration > 0:
                        return duration / timescale
    return None


def _read_pyav_duration_ms(path: Path) -> int | None:
    """Container duration via PyAV (the decoder faster-whisper already ships)."""
    try:
        import av
    except ImportError:  # pragma: no cover - av is a faster-whisper dependency
        return None
    try:
        with av.open(str(path)) as container:
            seconds = container.duration / 1_000_000 if container.duration else None
    except Exception as exc:  # noqa: BLE001 - any decode failure is a fallthrough
        log.warning("PyAV duration read failed for %s: %s", path.name, exc)
        return None
    if not seconds or seconds <= 0:
        return None
    return round(seconds * 1000)


def container_duration_ms(path: str | Path) -> int | None:
    """Header-only duration in milliseconds, or ``None`` if the header lies.

    Reads at most the first few MB of the file; ``moov`` may be at the end of
    a non-faststart file, so the whole file is scanned for box headers rather
    than trusting it to be in the first 64KB.
    """
    path = Path(path)
    try:
        buf = path.read_bytes()
    except OSError as exc:
        log.warning("duration: cannot read %s: %s", path, exc)
        return None
    seconds = _read_mvhd(buf)
    if seconds is None:
        seconds = _read_mehd(buf)
    if seconds is None or seconds * 1000 < MIN_PLAUSIBLE_DURATION_MS:
        log.warning(
            "duration: mvhd/mehd degenerate for %s (%s=%.6fs); "
            "caller must fall back to a decoder probe",
            path.name,
            "duration" if seconds is not None else "missing",
            seconds or 0.0,
        )
        return None
    return round(seconds * 1000)


def media_duration_ms(path: str | Path, *, allow_pyav: bool = True) -> int | None:
    """Authoritative duration in ms, or ``None`` -- logged loudly at ERROR.

    ``None`` is a hard stop for callers: without a duration the strict-T gate
    has no trustworthy upper bound, so a video pipeline must refuse to index
    rather than guess. See the module docstring.
    """
    path = Path(path)
    header_ms = container_duration_ms(path)
    if header_ms is not None and header_ms >= MIN_PLAUSIBLE_DURATION_MS:
        return header_ms
    if allow_pyav:
        probed = _read_pyav_duration_ms(path)
        if probed is not None:
            log.info(
                "duration: recovered %dms for %s from PyAV after header failure",
                probed,
                path.name,
            )
            return probed
    log.error(
        "duration UNAVAILABLE for %s: container header and decoder probe both "
        "failed. Refusing to index time-gated content without a duration "
        "(ADR-002 5.1). Strict-T cannot be enforced against an unknown "
        "timeline.",
        path.name,
    )
    return None