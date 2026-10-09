"""Duration is a safety input, so it gets tested against real bytes.

Tests 1 and 2 of the build brief:

1. ``test_duration_matches_known_media``  -- 655.5s +/- 1.0 for the real mp4
2. ``test_duration_never_silently_unavailable`` -- None AND a log line

(1) is deliberately a real-file test rather than a synthetic header. The
original bug -- reading ``mvhd`` at +12/+16 instead of +16/+20 -- produced a
*plausible-looking* number (100ms) that a mocked box would happily have
reproduced. Only the real container's timescale=1000/duration=655500 pins the
offsets.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from app.ingestion.duration import (
    MIN_PLAUSIBLE_DURATION_MS,
    container_duration_ms,
    media_duration_ms,
)

TEST_DATA = Path(__file__).resolve().parent.parent.parent.parent / "test-data"
REAL_MP4 = TEST_DATA / "Partial-derivatives.mp4"
REAL_PDF = TEST_DATA / "UNIT-1-001.pdf"

pytestmark = pytest.mark.skipif(
    not REAL_MP4.exists(), reason="test-data/Partial-derivatives.mp4 not present"
)


def test_duration_matches_known_media():
    """Header parse must reproduce the verified 655.5s within 1.0s.

    Asserted against BOTH paths: the container header alone, and the full
    resolver. They agree, which is what makes the header authoritative rather
    than merely plausible.
    """
    header_ms = container_duration_ms(REAL_MP4)
    assert header_ms is not None, "mvhd must parse for a real mp4"

    resolved_ms = media_duration_ms(REAL_MP4)
    assert resolved_ms is not None

    for label, value in (("container", header_ms), ("resolved", resolved_ms)):
        assert abs(value - 655_500) <= 1000, (
            f"{label} duration {value}ms is not within 1.0s of the verified "
            "655.5s -- mvhd offsets are probably wrong"
        )


def test_container_duration_needs_no_decoder(monkeypatch):
    """The header path must not import PyAV.

    Proven by making PyAV unimportable: duration comes from box parsing alone,
    so a machine with no av wheel still gets an authoritative timeline.
    """
    import builtins

    real_import = builtins.__import__

    def _blocked(name, *args, **kwargs):
        if name == "av" or name.startswith("av."):
            raise ImportError("av blocked for this test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocked)

    assert abs(container_duration_ms(REAL_MP4) - 655_500) <= 1000


def test_duration_never_silently_unavailable(tmp_path, caplog):
    """Unreadable media returns None AND logs at ERROR.

    Both halves matter. A ``None`` with no log line is indistinguishable from
    "no duration needed"; an ERROR with a return value is a caller bug. The
    message names the refusal so an operator reading logs knows indexing was
    declined on purpose.
    """
    junk = tmp_path / "not-media.mp4"
    junk.write_bytes(b"\x00\x01\x02\x03" * 64)

    with caplog.at_level(logging.ERROR, logger="app.ingestion.duration"):
        assert container_duration_ms(junk) is None
        assert media_duration_ms(junk) is None

    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors, "an unavailable duration MUST be logged at ERROR level"
    assert "duration UNAVAILABLE" in caplog.text
    assert "Refusing to index" in caplog.text


def test_missing_file_is_unavailable_and_logged(tmp_path, caplog):
    """A path that does not exist is the same class of failure."""
    with caplog.at_level(logging.WARNING, logger="app.ingestion.duration"):
        assert media_duration_ms(tmp_path / "nope.mp4") is None
    assert caplog.records, "a missing file must still log"


def test_documents_do_not_need_a_duration(tmp_path, caplog):
    """A PDF has no time axis; the resolver correctly declines to invent one.

    Included because it documents the deliberate asymmetry: strict-T applies to
    video/visual only, so refusing duration on a document is right, not a gap.
    """
    with caplog.at_level(logging.ERROR):
        assert media_duration_ms(REAL_PDF, allow_pyav=False) is None
    # ERROR is expected here too -- a caller that needs a duration for a
    # document is asking the wrong question, and gets told so.
    assert "duration UNAVAILABLE" in caplog.text


def test_degenerate_header_is_reported_as_degenerate(tmp_path, caplog):
    """A structurally valid box with a zero timescale is rejected, not divided.

    A zero timescale is the signature of reading the wrong offsets; the guard
    must catch it and say so rather than emitting an infinite or zero duration.
    """
    import struct

    # mvhd v0, timescale 0, duration 655500 -- the exact corruption the old
    # offset bug produced.
    mvhd = struct.pack(">I4s", 108, b"mvhd") + struct.pack(
        ">IIIIII", 0, 0, 0, 0, 655_500, 0x00010000
    )
    blob = struct.pack(">I4s", 8, b"free") + mvhd
    path = tmp_path / "zero-timescale.mp4"
    path.write_bytes(blob)

    with caplog.at_level(logging.WARNING):
        assert container_duration_ms(path) is None

    assert "degenerate" in caplog.text
    assert MIN_PLAUSIBLE_DURATION_MS >= 1