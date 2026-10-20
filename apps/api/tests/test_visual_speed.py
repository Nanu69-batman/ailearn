"""Speed work on the visual pass: dedupe, smaller frames, bounded parallelism.

No network: ``_gemini_describe`` and ``sample_frames`` are faked; frames are
synthetic PIL JPEGs.
"""

from __future__ import annotations

import io
import threading
import time

import pytest
from PIL import Image

from app.ingestion import visual as visual_mod

DURATION_MS = 655_500


def _jpeg(color: int = 200, size=(320, 240), box=None, box_color: int = 0) -> bytes:
    img = Image.new("RGB", size, (color, color, color))
    if box:
        img.paste((box_color,) * 3, box)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(visual_mod, "DESCRIBE_MIN_INTERVAL_S", 0.0)
    monkeypatch.setattr(visual_mod, "DESCRIBE_BACKOFF_S", 0.0)
    monkeypatch.setattr(visual_mod, "DESCRIBE_BACKOFF_CAP_S", 0.0)
    monkeypatch.setattr(visual_mod, "DEDUPE_ENABLED", True)
    monkeypatch.setattr(visual_mod, "DEDUPE_CELL_THRESHOLD", 4.0)
    monkeypatch.setattr(visual_mod, "DEDUPE_FORCE_KEEP_EVERY", 5)
    monkeypatch.setattr(visual_mod, "VISION_CONCURRENCY", 3)


def _sampler(monkeypatch, frames: dict[int, bytes]):
    def _sample(path, **kw):
        yield from sorted(frames.items())

    monkeypatch.setattr(visual_mod, "sample_frames", _sample)


def _describer(monkeypatch, sent: list | None = None, fn=None):
    def _d(frames):
        if sent is not None:
            sent.append([i for i, _ in frames])
        if fn is not None:
            return fn(frames)
        return {i: f"frame {i} content" for i, _ in frames}

    monkeypatch.setattr(visual_mod, "_gemini_describe", _d)


# --- dedupe ---------------------------------------------------------------


def test_identical_frames_are_skipped_and_first_always_kept(monkeypatch):
    same = _jpeg()
    _sampler(monkeypatch, {i: same for i in range(4)})
    sent: list = []
    _describer(monkeypatch, sent)
    rep = visual_mod.extract_visual_pass("x.mp4", duration_ms=DURATION_MS, start_ms=0, end_ms=16_000)
    assert [i for b in sent for i in b] == [0]
    assert rep.skipped_indices == [1, 2, 3]
    assert rep.skipped_duplicates == 3


def test_small_localised_change_in_one_cell_is_kept(monkeypatch):
    base = _jpeg()
    # ~one 8x8-grid cell (40x30 px of a 320x240 image), strong contrast.
    changed = _jpeg(box=(0, 0, 40, 30), box_color=0)
    _sampler(monkeypatch, {0: base, 1: base, 2: changed, 3: changed})
    sent: list = []
    _describer(monkeypatch, sent)
    rep = visual_mod.extract_visual_pass("x.mp4", duration_ms=DURATION_MS, start_ms=0, end_ms=16_000)
    assert [i for b in sent for i in b] == [0, 2]
    assert rep.skipped_indices == [1, 3]


def test_whole_image_average_is_not_the_test():
    base = _jpeg()
    changed = _jpeg(box=(0, 0, 40, 30), box_color=0)
    a = visual_mod._signature(visual_mod._open_image(base))
    b = visual_mod._signature(visual_mod._open_image(changed))
    mean_all = sum(abs(p - q) for p, q in zip(a, b)) / len(a)
    assert mean_all < visual_mod.DEDUPE_CELL_THRESHOLD  # global avg would miss it
    assert visual_mod.frames_differ(a, b)


def test_forced_keep_every_fifth_frame(monkeypatch):
    same = _jpeg()
    _sampler(monkeypatch, {i: same for i in range(11)})
    sent: list = []
    _describer(monkeypatch, sent)
    rep = visual_mod.extract_visual_pass("x.mp4", duration_ms=DURATION_MS, start_ms=0, end_ms=44_000)
    assert [i for b in sent for i in b] == [0, 5, 10]
    assert rep.skipped_indices == [1, 2, 3, 4, 6, 7, 8, 9]


def test_span_end_extends_to_last_covered_frame_and_is_clamped(monkeypatch):
    same = _jpeg()
    _sampler(monkeypatch, {i: same for i in range(4)})
    _describer(monkeypatch)
    rep = visual_mod.extract_visual_pass("x.mp4", duration_ms=DURATION_MS, start_ms=0, end_ms=16_000)
    (seg,) = rep.segments
    assert (seg.frame_index, seg.start_ms, seg.end_ms) == (0, 0, 16_000)

    # Window end not on a frame boundary: never beyond the window end.
    rep = visual_mod.extract_visual_pass("x.mp4", duration_ms=DURATION_MS, start_ms=0, end_ms=14_000)
    assert rep.segments[0].end_ms <= 14_000

    # Duration shorter than the last covered frame's interval: clamped.
    rep = visual_mod.extract_visual_pass("x.mp4", duration_ms=13_000, start_ms=0, end_ms=16_000)
    assert rep.segments[0].end_ms <= 13_000


def test_skipped_frames_are_covered_not_failed(monkeypatch):
    same = _jpeg()
    _sampler(monkeypatch, {i: same for i in range(5)})
    _describer(monkeypatch)
    rep = visual_mod.extract_visual_pass("x.mp4", duration_ms=DURATION_MS, start_ms=0, end_ms=20_000)
    assert rep.status == "complete"
    assert rep.coverage_pct == 100.0
    assert rep.failures == []
    assert rep.covered_indices == rep.expected_indices


def test_skipped_frames_of_a_failed_batch_stay_uncovered(monkeypatch):
    same = _jpeg()
    _sampler(monkeypatch, {i: same for i in range(5)})

    def boom(frames):
        raise ValueError("permanent boom")

    _describer(monkeypatch, fn=boom)
    rep = visual_mod.extract_visual_pass("x.mp4", duration_ms=DURATION_MS, start_ms=0, end_ms=20_000)
    assert rep.status == "failed"
    assert rep.covered_indices == []
    assert rep.failures


def test_kept_indices_are_all_sampler_indices(monkeypatch):
    frames = {i: _jpeg(color=50 * (i % 2) + 100) for i in range(10, 20)}
    _sampler(monkeypatch, frames)
    sent: list = []
    _describer(monkeypatch, sent)
    rep = visual_mod.extract_visual_pass(
        "x.mp4", duration_ms=DURATION_MS, start_ms=40_000, end_ms=80_000
    )
    assert {i for b in sent for i in b} <= set(rep.sampled_indices)
    assert {s.frame_index for s in rep.segments} <= set(rep.sampled_indices)


def test_dedupe_can_be_disabled(monkeypatch):
    monkeypatch.setattr(visual_mod, "DEDUPE_ENABLED", False)
    same = _jpeg()
    _sampler(monkeypatch, {i: same for i in range(4)})
    sent: list = []
    _describer(monkeypatch, sent)
    rep = visual_mod.extract_visual_pass("x.mp4", duration_ms=DURATION_MS, start_ms=0, end_ms=16_000)
    assert [i for b in sent for i in b] == [0, 1, 2, 3]
    assert rep.skipped_duplicates == 0


# --- downscale --------------------------------------------------------------


def test_shrink_caps_long_side_at_768():
    big = _jpeg(size=(1920, 1080))
    out = visual_mod.shrink_frame(big)
    assert max(Image.open(io.BytesIO(out)).size) == 768
    assert len(out) < len(big)


def test_shrink_leaves_small_and_undecodable_frames_alone():
    small = _jpeg(size=(400, 300))
    assert visual_mod.shrink_frame(small) == small
    assert visual_mod.shrink_frame(b"jpeg") == b"jpeg"


# --- concurrency ------------------------------------------------------------


def _distinct_frames(n: int) -> dict[int, bytes]:
    # Alternate strongly different frames so nothing is deduped.
    return {i: _jpeg(color=40 if i % 2 else 220) for i in range(n)}


def test_batches_run_in_parallel_and_merge_in_order(monkeypatch):
    n_batches = 6
    _sampler(monkeypatch, _distinct_frames(n_batches * 2))

    def slow(frames):
        time.sleep(0.2)
        return {i: f"d{i}" for i, _ in frames}

    _describer(monkeypatch, fn=slow)
    t0 = time.monotonic()
    rep = visual_mod.extract_visual_pass(
        "x.mp4", duration_ms=DURATION_MS, batch_size=2, start_ms=0, end_ms=48_000
    )
    elapsed = time.monotonic() - t0
    serial = n_batches * 0.2
    assert elapsed < serial * 0.65, (elapsed, serial)
    assert rep.described_indices == list(range(12))
    assert [s.frame_index for s in rep.segments] == list(range(12))
    assert rep.timings["concurrency"] == 3
    assert len(rep.timings["vision_calls"]) == n_batches


def test_retryable_error_pauses_all_workers(monkeypatch):
    monkeypatch.setattr(visual_mod, "DESCRIBE_BACKOFF_S", 0.5)
    monkeypatch.setattr(visual_mod, "DESCRIBE_BACKOFF_CAP_S", 0.5)
    _sampler(monkeypatch, _distinct_frames(8))
    lock = threading.Lock()
    starts: list[tuple[float, int]] = []
    failed_at: list[float] = []

    def flaky(frames):
        now = time.monotonic()
        first = frames[0][0]
        with lock:
            starts.append((now, first))
            if first == 0 and not failed_at:
                failed_at.append(now)
                raise RuntimeError("429 RESOURCE_EXHAUSTED")
        time.sleep(0.05)
        return {i: "ok" for i, _ in frames}

    _describer(monkeypatch, fn=flaky)
    rep = visual_mod.extract_visual_pass(
        "x.mp4", duration_ms=DURATION_MS, batch_size=2, start_ms=0, end_ms=32_000
    )
    assert rep.status == "complete"
    t_fail = failed_at[0]
    # Every call that started after the 429 waited for the shared pause.
    later = [t for t, _ in starts if t > t_fail + 1e-6]
    assert later and min(later) >= t_fail + 0.5 - 0.02
    assert rep.timings["retry_wait_s"] >= 0.45


def test_call_starts_are_spaced_globally(monkeypatch):
    monkeypatch.setattr(visual_mod, "DESCRIBE_MIN_INTERVAL_S", 0.15)
    _sampler(monkeypatch, _distinct_frames(8))
    starts: list[float] = []

    def rec(frames):
        starts.append(time.monotonic())
        time.sleep(0.3)
        return {i: "ok" for i, _ in frames}

    _describer(monkeypatch, fn=rec)
    visual_mod.extract_visual_pass(
        "x.mp4", duration_ms=DURATION_MS, batch_size=2, start_ms=0, end_ms=32_000
    )
    gaps = [b - a for a, b in zip(sorted(starts), sorted(starts)[1:])]
    assert gaps and min(gaps) >= 0.15 - 0.02


def test_failing_batch_yields_partial_not_silence(monkeypatch):
    _sampler(monkeypatch, _distinct_frames(6))

    def fn(frames):
        if frames[0][0] == 2:
            raise ValueError("permanent failure")
        return {i: "ok" for i, _ in frames}

    _describer(monkeypatch, fn=fn)
    rep = visual_mod.extract_visual_pass(
        "x.mp4", duration_ms=DURATION_MS, batch_size=2, start_ms=0, end_ms=24_000
    )
    assert rep.status == "partial"
    assert rep.described_indices == [0, 1, 4, 5]
    assert any("frames 2-3" in f for f in rep.failures)


def test_worker_bug_is_not_swallowed(monkeypatch):
    _sampler(monkeypatch, _distinct_frames(2))
    _describer(monkeypatch)

    def bad_validate(result, sent):
        raise RuntimeError("validator bug")

    monkeypatch.setattr(visual_mod, "_validated_descriptions", bad_validate)
    with pytest.raises(RuntimeError, match="validator bug"):
        visual_mod.extract_visual_pass(
            "x.mp4", duration_ms=DURATION_MS, batch_size=2, start_ms=0, end_ms=8_000
        )


def test_timings_are_reported(monkeypatch):
    _sampler(monkeypatch, _distinct_frames(4))
    _describer(monkeypatch)
    rep = visual_mod.extract_visual_pass(
        "x.mp4", duration_ms=DURATION_MS, batch_size=2, start_ms=0, end_ms=16_000
    )
    t = rep.timings
    for key in ("sample_s", "vision_calls", "retry_wait_s", "total_s", "frames_sent"):
        assert key in t
    assert t["frames_sent"] == 4
