"""Windowed audio/visual passes and window-safe chunk ordinals (no network)."""

import re
import sys
import types

import pytest

from app.ingestion import audio as audio_mod
from app.ingestion import jobs
from app.ingestion import visual as visual_mod

DUR = 240_000


def test_windows_concat_equals_monolithic_and_each_complete():
    mono = visual_mod.iter_frame_indices(DUR)
    w1 = visual_mod.window_frame_indices(DUR, start_ms=0, end_ms=120_000)
    w2 = visual_mod.window_frame_indices(DUR, start_ms=120_000, end_ms=240_000)
    assert w1 + w2 == mono
    for lo, hi in ((0, 120_000), (120_000, 240_000)):
        rep = visual_mod.extract_visual_pass(
            "x.mp4", duration_ms=DUR, describe=False, start_ms=lo, end_ms=hi
        )
        assert rep.expected_indices == visual_mod.window_frame_indices(
            DUR, start_ms=lo, end_ms=hi
        )
    assert visual_mod.window_frame_indices(DUR) == mono


def test_out_of_window_index_dropped_not_clamped(monkeypatch):
    def fake_sampler(path, *, interval_s, start_ms=None, end_ms=None):
        for i in visual_mod.window_frame_indices(
            DUR, interval_s=interval_s, start_ms=start_ms, end_ms=end_ms
        ):
            yield i, b"jpg"

    def fake_describe(items):
        return {items[0][0]: "slide one", 999: "stray"}, ""

    monkeypatch.setattr(visual_mod, "sample_frames", fake_sampler)
    monkeypatch.setattr(visual_mod, "_describe_with_retry", fake_describe)
    monkeypatch.setattr(visual_mod, "DESCRIBE_MIN_INTERVAL_S", 0)
    rep = visual_mod.extract_visual_pass(
        "x.mp4", duration_ms=DUR, start_ms=0, end_ms=120_000, batch_size=100
    )
    assert rep.described_indices == [0]
    assert 999 not in rep.described_indices
    assert any("999" in f for f in rep.failures)
    assert all(s.frame_index == 0 for s in rep.segments)


class _Seg:
    def __init__(self, s, e, t):
        self.start, self.end, self.text = s, e, t


def test_audio_window_cached_model_clip_and_no_gemini(monkeypatch):
    built, calls = [], []

    class FakeModel:
        def __init__(self, size, **kw):
            built.append(size)

        def transcribe(self, path, **kw):
            calls.append(kw)
            return iter(
                [_Seg(130.0, 135.0, "in"), _Seg(10.0, 12.0, "before"),
                 _Seg(300.0, 301.0, "after")]
            ), None

    fake = types.ModuleType("faster_whisper")
    fake.WhisperModel = FakeModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fake)
    monkeypatch.setattr(audio_mod, "_WHISPER_MODELS", {})
    monkeypatch.setattr(audio_mod, "_shim_pyav_metadata_errors", lambda: None)

    def boom(*a, **k):
        raise AssertionError("gemini must not be called for a window")

    monkeypatch.setattr(audio_mod, "transcribe_with_gemini", boom)
    kw = {"duration_ms": DUR, "allow_gemini": True, "start_ms": 120_000, "end_ms": 240_000}
    segs, _ = audio_mod.transcribe("a.mp4", **kw)
    audio_mod.transcribe("a.mp4", **kw)
    assert [s.text for s in segs] == ["in"]
    assert segs[0].start_s == 130.0
    assert len(built) == 1
    assert calls[0]["clip_timestamps"] == "120.0,240.0"


def _vis(i):
    return visual_mod.VisualSegment(
        frame_index=i, text="same text", start_ms=i * 4000, end_ms=i * 4000 + 4000
    )


def _ids(chunks):
    return [c.chunk_id for c in chunks]


def _ordinals(chunks):
    return [int(re.search(r"_c(\d+)_", c).group(1)) for c in _ids(chunks)]


def test_chunk_ids_unique_and_monotonic_across_windows():
    a = jobs.build_chunks(resource_id="r", course_id="c", source_type="video",
                          visual=[_vis(0), _vis(1)], index_offset=jobs.window_index_offset(0))
    b = jobs.build_chunks(resource_id="r", course_id="c", source_type="video",
                          visual=[_vis(0), _vis(1)],
                          index_offset=jobs.window_index_offset(120_000))
    assert not set(_ids(a)) & set(_ids(b))
    assert max(_ordinals(a)) < min(_ordinals(b))
    default = jobs.build_chunks(resource_id="r", course_id="c", source_type="video",
                                visual=[_vis(0)])
    assert _ordinals(default)[0] == 0


def test_overflow_guard_trips():
    with pytest.raises(ValueError):
        jobs.build_chunks(resource_id="r", course_id="c", source_type="video",
                          visual=[_vis(0), _vis(1), _vis(2)], max_chunks=2)
    assert jobs.window_max_chunks(120_000, 240_000) == 120_000
