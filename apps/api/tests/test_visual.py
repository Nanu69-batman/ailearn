"""Visual segment timestamps come from frame index arithmetic, never the model.

Build-brief tests 8 and 9:

8. ``test_visual_segment_timestamps_come_from_frame_index``
9. ``test_visual_segments_carry_start_and_end_ms``

Plus test 10 (``test_gemini_timeline_overshoot_is_rejected_or_rescaled``),
which covers the audio side of the same "a model's word is not evidence" rule.

No network: ``_gemini_describe`` is monkeypatched throughout, so no test here
can call Gemini. faster-whisper's model download is likewise never triggered --
:func:`app.ingestion.audio.transcribe` is exercised with both engines stubbed.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from app.ingestion import audio as audio_mod
from app.ingestion import visual as visual_mod
from app.ingestion.chunking import TextSegment, chunk_segment, split_segment
from app.ingestion.visual import (
    FRAME_INTERVAL_S,
    build_visual_segments,
    frame_bounds,
)

DURATION_MS = 655_500


def test_frame_bounds_is_pure_index_arithmetic():
    """The function that owns visual timestamps contains no model calls at all.

    Asserted structurally: sample some indices, then assert the bounds are
    exactly ``index * interval``. If someone later routes model output into
    this function, this test fails.
    """
    interval_ms = int(FRAME_INTERVAL_S * 1000)
    for index in (0, 1, 7, 60, 163):
        start, end = frame_bounds(index, duration_ms=DURATION_MS)
        assert start == index * interval_ms
        # The last sampled frame is clamped to the duration, so it is the one
        # index whose end is not the plain arithmetic -- and even then it is
        # clamped DOWN, never up.
        expected_end = min((index + 1) * interval_ms, DURATION_MS)
        assert end == expected_end
        assert end <= (index + 1) * interval_ms


def test_frame_bounds_clamped_to_duration():
    """A frame straddling the end of the file cannot claim content past it."""
    last = DURATION_MS // int(FRAME_INTERVAL_S * 1000)
    start, end = frame_bounds(last + 5, duration_ms=DURATION_MS)
    assert end <= DURATION_MS, "frame bounds exceeded the authoritative duration"
    assert start <= DURATION_MS


def test_frame_bounds_always_nonzero_extent():
    """Every frame has positive extent, even clamped at the duration boundary.

    A zero-length span could never satisfy ``end_ms <= T`` at a boundary, and
    the gate compares on end_ms -- so a degenerate span would be silently
    unreachable rather than correct.
    """
    for index in (0, 1, 200, 100_000):
        start, end = frame_bounds(index, duration_ms=DURATION_MS)
        assert end > start, f"frame {index} produced a zero-length span"


def test_visual_segment_timestamps_come_from_frame_index():
    """THE critical visual test: timestamps ignore what the model said.

    The fake descriptor deliberately returns descriptions containing
    timestamps and out-of-order indices. The segments must still be timed
    purely from frame index -- ascending, exactly ``index * interval``, with no
    influence whatsoever from the text.
    """
    # A model that hallucinates timecodes and reorders frames.
    described = {
        3: "on screen at 00:14:22 the chain rule is displayed",
        1: "on screen at 99:99:99 nonsense timestamp",
        0: "slide title: partial derivatives, timestamp claims 500s",
        2: "graph of a curve, timestamp claims 2s",
    }

    segments = build_visual_segments(described, duration_ms=DURATION_MS)

    assert [s.frame_index for s in segments] == [0, 1, 2, 3], (
        "segments must be ordered by frame index, not by any model ordering"
    )
    interval_ms = int(FRAME_INTERVAL_S * 1000)
    for seg in segments:
        # The bounds are exactly the index arithmetic. The descriptions assert
        # wildly different times (0s, 2s, 500s, 99:99:99) and none of them
        # reach the bounds -- so a leak from the text into the timestamps
        # would show up as these equalities failing.
        assert seg.start_ms == seg.frame_index * interval_ms
        assert seg.end_ms == (seg.frame_index + 1) * interval_ms
        assert seg.start_ms != 500_000
        assert seg.start_ms != 862_000

    assert segments[0].start_ms == 0
    assert segments[1].start_ms == interval_ms
    assert segments[3].start_ms == 3 * interval_ms


def test_visual_segments_carry_start_and_end_ms():
    """Every visual segment has non-null, in-range bounds.

    Non-null is the requirement: a NULL ``end_ms`` on a time-gated chunk is
    EXCLUDE (ADR-002 §5.1), so a visual segment missing bounds would be
    invisible to retrieval rather than safely gated. And both bounds must stay
    inside the authoritative duration.
    """
    described = {i: f"frame {i} shows a plotted curve" for i in range(20)}
    segments = build_visual_segments(described, duration_ms=DURATION_MS)

    assert len(segments) == 20
    for seg in segments:
        assert seg.start_ms is not None, "visual segment has NULL start_ms"
        assert seg.end_ms is not None, "visual segment has NULL end_ms"
        assert seg.start_ms >= 0
        assert seg.end_ms <= DURATION_MS, "visual segment exceeds media duration"
        assert seg.end_ms > seg.start_ms


def test_visual_segments_survive_chunking_with_bounds_intact():
    """Chunk splitting must not strip or widen a visual segment's bounds.

    Splitting is allowed to shrink the window; losing the bounds entirely would
    make the chunk unretrievable, and widening them would be a spoiler.
    """
    long_text = "the slide shows the chain rule equation " * 200
    described = {5: long_text}
    segments = build_visual_segments(described, duration_ms=DURATION_MS)
    segment = segments[0]

    chunks = chunk_segment(
        TextSegment(
            text=segment.text,
            start_ms=segment.start_ms,
            end_ms=segment.end_ms,
            kind="visual",
        ),
        resource_id="res_video",
        course_id="course_1",
        source_type="video",
        video_id="vid_1",
    )

    assert len(chunks) > 1, "a 6000-char description must actually split"
    for chunk in chunks:
        assert chunk.start_ms is not None and chunk.end_ms is not None
        assert segment.start_ms <= chunk.start_ms <= chunk.end_ms <= segment.end_ms, (
            f"chunk bounds {chunk.start_ms}..{chunk.end_ms} escaped the source "
            f"segment {segment.start_ms}..{segment.end_ms}"
        )


def test_empty_descriptions_are_dropped_not_indexed():
    """A frame with nothing legible is skipped rather than indexed as noise.

    The model is told to omit what it cannot see; an "empty" description
    therefore carries no retrievable content and would only dilute results.
    """
    described = {0: "a graph of a curve", 1: "no visible content", 2: "  "}
    segments = build_visual_segments(described, duration_ms=DURATION_MS)
    assert [s.frame_index for s in segments] == [0]

    kept = build_visual_segments(
        described, duration_ms=DURATION_MS, keep_empty=True
    )
    assert [s.frame_index for s in kept] == [0, 1, 2]


def test_iter_frame_indices_covers_the_duration():
    """Frame count derives from duration; the last frame is inside the file."""
    indices = visual_mod.iter_frame_indices(DURATION_MS)
    interval_ms = int(FRAME_INTERVAL_S * 1000)
    assert indices[0] == 0
    assert indices == sorted(indices)
    last = indices[-1]
    assert frame_bounds(last, duration_ms=DURATION_MS)[1] <= DURATION_MS
    # One more index would run past the end.
    assert (last + 1) * interval_ms > DURATION_MS
    assert visual_mod.iter_frame_indices(0) == []


def test_container_duration_is_milliseconds_not_seconds():
    """PyAV reports MICROSECONDS; a /1e6 slip returns seconds.

    This is a real bug that shipped and silently disabled almost the entire
    visual pass: 655499320us -> 655 (seconds) -> the sampler saw a 655ms
    "duration", computed one frame index, and indexed exactly one frame of a
    655-second lecture -- with no error anywhere.
    """
    class FakeContainer:
        duration = 655_499_320  # microseconds, as PyAV reports it

    assert visual_mod._container_duration_ms(FakeContainer()) == 655_499

    class NoDuration:
        duration = None

    assert visual_mod._container_duration_ms(NoDuration()) == 0


def test_real_media_produces_many_frame_indices():
    """Sanity guard on the real file: a 655s lecture must yield 164 indices.

    Under the microseconds/seconds bug this returned 1.
    """
    media = (
        Path(__file__).resolve().parent.parent.parent.parent
        / "test-data"
        / "Partial-derivatives.mp4"
    )
    if not media.exists():
        pytest.skip("test media not present")

    import av

    with av.open(str(media)) as container:
        duration_ms = visual_mod._container_duration_ms(container)
    assert duration_ms == 655_499

    indices = visual_mod.iter_frame_indices(duration_ms)
    assert len(indices) == 164, (
        f"expected 164 frame indices for a 655.5s file at 4s, got {len(indices)}"
    )
    assert indices[0] == 0 and indices[-1] == 163


def test_describe_false_never_calls_the_model(monkeypatch):
    """``describe=False`` is a hard no-model guarantee.

    The batching loop used to flush the first batch even when ``describe`` was
    False, so a single-frame sample still called Gemini -- which would have made
    the offline path untestable and, in production, billed a model call for
    nothing. ``sample_frames`` is made to *raise* so any decode at all fails the
    test too.
    """
    def _explode(*a, **kw):
        raise AssertionError("describe=False must not sample frames")

    def _no_model(frames):
        raise AssertionError("describe=False must not call Gemini")

    monkeypatch.setattr(visual_mod, "sample_frames", _explode)
    monkeypatch.setattr(visual_mod, "_gemini_describe", _no_model)

    assert (
        visual_mod.extract_visual_segments(
            "fake.mp4", duration_ms=10_000, describe=False
        )
        == []
    )


def test_describe_true_batches_frames(monkeypatch):
    """With ``describe=True``, frames are batched and the model is consulted.

    Asserts the batch boundary (8 frames -> 2 calls of 4) so a batching change
    that silently stopped describing frames would be caught.
    """
    batches: list = []
    monkeypatch.setattr(
        visual_mod,
        "_gemini_describe",
        lambda frames: (
            batches.append([i for i, _ in frames])
            or {i: f"frame {i} content" for i, _ in frames}
        ),
    )
    monkeypatch.setattr(
        visual_mod,
        "sample_frames",
        lambda path, **kw: iter((i, b"jpeg") for i in range(10)),
    )

    segments = visual_mod.extract_visual_segments(
        "fake.mp4", duration_ms=60_000, describe=True, batch_size=4
    )

    assert batches == [[0, 1, 2, 3], [4, 5, 6, 7], [8, 9]]
    assert len(segments) == 10
    for seg in segments:
        assert seg.start_ms == seg.frame_index * 4_000


# ---------------------------------------------------------------------------
# Gemini timeline reconciliation (build-brief test 10)
# ---------------------------------------------------------------------------


def test_gemini_timeline_overshoot_is_rejected_or_rescaled():
    """Three cases: within duration, overshoot within tolerance, overshoot beyond.

    The beyond-tolerance case must be REJECTED -- an empty segment list, not a
    rescale. Rescaling a timeline that runs 37% past the end of the file would
    silently compress every timestamp in the transcript, and the strict-T gate
    compares on ``end_ms``, so it would be gating against fiction.
    """
    duration_s = 655.5

    inside = [
        audio_mod.TranscriptSegment(0.0, 10.0, "welcome"),
        audio_mod.TranscriptSegment(10.0, 640.0, "the chain rule"),
    ]
    verdict = audio_mod.reconcile_gemini_timeline(inside, duration_s)
    assert verdict.accepted
    assert not verdict.rescaled
    assert max(s.end_s for s in verdict.segments) <= duration_s

    # 1% overshoot: inside the 5% tolerance, so rescaled down to fit.
    small = [audio_mod.TranscriptSegment(0.0, 662.0, "slightly long")]
    small_verdict = audio_mod.reconcile_gemini_timeline(small, duration_s)
    assert small_verdict.accepted
    assert small_verdict.rescaled, "a within-tolerance overshoot must rescale"
    assert max(s.end_s for s in small_verdict.segments) <= duration_s

    # 37% overshoot: rejected outright. The tolerance is a constant, not a knob
    # to relax in order to make this pass.
    way_off = [audio_mod.TranscriptSegment(0.0, 900.0, "hallucinated tail")]
    hard = audio_mod.reconcile_gemini_timeline(way_off, duration_s)
    assert not hard.accepted, "a 900s timeline on a 655.5s file must be rejected"
    assert hard.segments == [], "a rejected timeline must yield no segments"
    assert "exceeds duration" in hard.reason
    assert hard.duration_s == duration_s


def test_rejection_is_not_silently_relaxed(monkeypatch):
    """Guards the tolerance against being widened to force a pass.

    Doubling the overshoot tolerance must NOT rescue the same timeline -- that
    is the "do not relax the tolerance" instruction made executable. The
    production value is asserted last so a test-side monkeypatch cannot mask a
    change to it.
    """
    segments = [audio_mod.TranscriptSegment(0.0, 900.0, "tail")]
    assert not audio_mod.reconcile_gemini_timeline(segments, 655.5).accepted

    # The shipped tolerance is 5% of runtime, plus a 1s floor for short clips.
    shipped = audio_mod.GEMINI_TIMELINE_OVERSHOOT_TOLERANCE_S
    monkeypatch.setattr(audio_mod, "GEMINI_TIMELINE_OVERSHOOT_TOLERANCE_S", 0.4)
    # 40% of 655.5 = 262.2s of budget, and 900s is 244.5s over, so this now
    # passes -- proving the constant is load bearing rather than decorative.
    widened = audio_mod.reconcile_gemini_timeline(segments, 655.5)
    assert widened.accepted and widened.rescaled
    assert max(s.end_s for s in widened.segments) <= 655.5

    assert shipped == 0.05, "the shipped overshoot tolerance must stay at 5%"


def test_no_duration_accepts_but_flags_the_timeline(caplog):
    """An unknown duration is accepted but visibly marked, not silently trusted."""
    segments = [audio_mod.TranscriptSegment(0.0, 500.0, "unverified")]
    with caplog.at_level(logging.INFO):
        verdict = audio_mod.reconcile_gemini_timeline(segments, None)
    assert verdict.accepted
    assert "duration unknown" in verdict.reason


def test_timestamp_agreement_reports_drift(caplog):
    """Drift between engines is reported, not absorbed.

    Also asserts the no-comparable-segments branch: an engine that returned
    nothing must not be reported as agreement.
    """
    whisper = [
        audio_mod.TranscriptSegment(0.0, 10.0, "a"),
        audio_mod.TranscriptSegment(10.0, 20.0, "b"),
    ]
    drifted = [
        audio_mod.TranscriptSegment(0.0, 14.0, "a"),
        audio_mod.TranscriptSegment(14.0, 25.0, "b"),
    ]

    with caplog.at_level(logging.WARNING):
        report = audio_mod.timestamp_agreement(whisper, drifted)

    assert report["comparable"]
    assert report["max_delta_ms"] == 5_000
    assert report["drifting_segments"] == 2
    assert "disagreement" in caplog.text

    assert audio_mod.timestamp_agreement(whisper, [])["comparable"] is False


def test_transcribe_falls_back_to_gemini_without_downloading_anything(monkeypatch):
    """No whisper model download, no Gemini call, in a test.

    ``transcribe_with_whisper`` is stubbed to raise exactly as it would if the
    model were unavailable, which proves the fallback path works without
    touching the network or the HF cache.
    """
    def _no_whisper(path, **kw):
        raise RuntimeError("model download unavailable in tests")

    monkeypatch.setattr(audio_mod, "transcribe_with_whisper", _no_whisper)

    def _no_gemini(path, **kw):
        raise AssertionError("tests must not call Gemini")

    monkeypatch.setattr(audio_mod, "transcribe_with_gemini", _no_gemini)

    segments, signals = audio_mod.transcribe(
        "fake.mp4", duration_ms=100_000, allow_gemini=False
    )

    assert segments == []
    assert signals["engine"] == "none"
    assert signals["whisper_error"] is not None
    assert "model download unavailable" in signals["whisper_error"]


def test_transcribe_prefers_whisper_and_reports_agreement(monkeypatch):
    """Whisper segments win when available; the agreement signal is emitted."""
    whisper = [audio_mod.TranscriptSegment(0.0, 5.0, "from whisper")]
    gemini = [audio_mod.TranscriptSegment(0.0, 5.2, "from gemini")]

    monkeypatch.setattr(
        audio_mod, "transcribe_with_whisper", lambda path, **kw: whisper
    )
    monkeypatch.setattr(
        audio_mod, "transcribe_with_gemini", lambda path, **kw: gemini
    )

    segments, signals = audio_mod.transcribe("fake.mp4", duration_ms=100_000)

    assert segments == whisper
    assert signals["engine"] == "whisper"
    assert signals["gemini_segments"] == 1
    assert signals["timestamp_agreement"]["comparable"] is True
    assert signals["timestamp_agreement"]["max_delta_ms"] == 200


def test_transcribe_uses_gemini_when_whisper_fails(monkeypatch):
    """The documented fallback: Gemini audio with reconciliation, not faked."""
    def _no_whisper(path, **kw):
        raise RuntimeError("no model")

    monkeypatch.setattr(audio_mod, "transcribe_with_whisper", _no_whisper)
    monkeypatch.setattr(
        audio_mod,
        "transcribe_with_gemini",
        lambda path, **kw: [audio_mod.TranscriptSegment(0.0, 60.0, "gemini audio")],
    )

    segments, signals = audio_mod.transcribe("fake.mp4", duration_ms=65_550)

    assert [s.text for s in segments] == ["gemini audio"]
    assert signals["engine"] == "gemini"
    assert signals["whisper_segments"] == 0


def test_transcribe_discards_a_rejected_gemini_timeline(monkeypatch, caplog):
    """A rejected Gemini timeline contributes nothing, and says so at ERROR."""
    monkeypatch.setattr(
        audio_mod,
        "transcribe_with_whisper",
        lambda path, **kw: (_ for _ in ()).throw(RuntimeError("no model")),
    )
    monkeypatch.setattr(
        audio_mod,
        "transcribe_with_gemini",
        lambda path, **kw: [audio_mod.TranscriptSegment(0.0, 900.0, "too long")],
    )

    with caplog.at_level(logging.ERROR):
        segments, signals = audio_mod.transcribe("fake.mp4", duration_ms=65_550)

    assert segments == []
    assert signals["gemini_segments"] == 0
    assert "rejected" in caplog.text


def test_gemini_json_parser_rejects_rubbish():
    """Unparseable model output yields no segments rather than an exception."""
    assert audio_mod._parse_gemini_segments("no json here") == []
    assert audio_mod._parse_gemini_segments('{"segments": [{"bad": 1}]}') == []
    parsed = audio_mod._parse_gemini_segments(
        '{"segments": [{"start": 1, "end": 2, "text": "hi"}, '
        '{"start": 3, "end": 4}]}'
    )
    assert [s.text for s in parsed] == ["hi"], "cues with no text must be dropped"


def test_visual_parser_rejects_rubbish():
    """Same contract on the visual side: bad indices are dropped, not guessed."""
    assert visual_mod._parse_frame_descriptions("garbage") == {}
    assert visual_mod._parse_frame_descriptions('{"frames": [{"index": "x"}]}') == {}
    parsed = visual_mod._parse_frame_descriptions(
        '{"frames": [{"index": 4, "description": "a curve"}, {"index": 7}]}'
    )
    assert parsed == {4: "a curve", 7: ""}


# ---------------------------------------------------------------------------
# Chunking: end_ms never exceeds the last observed moment
# ---------------------------------------------------------------------------


def test_split_never_widens_a_timestamp():
    """Splitting may shrink the window; it may never grow it."""
    segment = TextSegment(
        text="partial derivative " * 400, start_ms=58_000, end_ms=61_000
    )
    pieces = split_segment(segment)

    assert len(pieces) > 1
    for piece in pieces:
        assert segment.start_ms <= piece.start_ms <= segment.end_ms
        assert segment.start_ms <= piece.end_ms <= segment.end_ms
    # Bounds must be non-decreasing and inside the parent window.
    assert [p.start_ms for p in pieces] == sorted(p.start_ms for p in pieces)


def test_split_of_documents_carries_no_time_bounds():
    """Documents split freely -- there is no window to widen or exceed."""
    segment = TextSegment(
        text="the chain rule for composite functions " * 200, page_no=7
    )
    pieces = split_segment(segment)
    assert len(pieces) > 1
    for piece in pieces:
        assert piece.start_ms is None and piece.end_ms is None
        assert piece.page_no == 7


def test_blank_segments_produce_no_chunks():
    """Empty text must not become a citation-shaped nothing."""
    assert split_segment(TextSegment(text="   ", start_ms=0, end_ms=1_000)) == []
    assert (
        chunk_segment(
            TextSegment(text="", start_ms=0, end_ms=1_000),
            resource_id="r", course_id="c", source_type="video",
        )
        == []
    )


@pytest.mark.parametrize("end_ms", [1_001, 2_000, 60_000, 655_500])
def test_chunk_bounds_are_always_coherent(end_ms):
    """Any valid end yields start <= end within the same segment."""
    segment = TextSegment(
        text="a short but non-trivial sentence about derivatives " * 30,
        start_ms=1_000,
        end_ms=end_ms,
    )
    pieces = split_segment(segment)
    assert pieces
    for piece in pieces:
        assert piece.start_ms is not None and piece.end_ms is not None
        assert piece.start_ms <= piece.end_ms
        assert piece.end_ms <= end_ms, "a split widened past the segment end"


@pytest.mark.parametrize(("start_ms", "end_ms"), [(1_000, None), (None, 2_000)])
def test_half_bounded_time_segments_are_dropped_not_repaired(
    start_ms, end_ms, caplog
):
    """Half-bounded time content yields no chunks.

    The two tempting "fixes" are both spoiler bugs -- fabricating the missing
    bound claims observation that never happened, and stripping the timestamps
    would make a video chunk look like an ungated document and slip it past
    strict-T entirely. Dropping it, loudly, is the only safe option.
    """
    segment = TextSegment(
        text="content about partial derivatives " * 40,
        start_ms=start_ms,
        end_ms=end_ms,
    )
    with caplog.at_level(logging.ERROR):
        assert split_segment(segment) == []
    assert caplog.records, "a malformed time segment must be logged"


def test_zero_length_segment_is_dropped_never_widened(caplog):
    """``start_ms == end_ms`` has no extent to split into, so it yields nothing.

    The alternative -- granting each piece 1ms of extent -- would mean
    asserting that content was observed during a period when none was, and
    ``end_ms`` is exactly the field the strict-T gate trusts. Dropping is the
    honest failure and it is logged.
    """
    segment = TextSegment(
        text="content about partial derivatives " * 40,
        start_ms=5_000,
        end_ms=5_000,
    )
    with caplog.at_level(logging.WARNING):
        assert split_segment(segment) == []
    assert "zero-length segment" in caplog.text