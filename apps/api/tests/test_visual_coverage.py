"""The visual pass must cover the whole video, and must say so when it does not.

Regression guard for a shipped bug. ``res_95f6dc70e36a`` (an 11-minute
lecture) was indexed with 89 transcript chunks and 40 visual chunks, and the
visual 40 sat at 0, 4000, 8000 ... 156000 ms -- a contiguous 4-second walk from
the start, covering 24.4% of the file. The last eight minutes had audio and no
on-screen description at all, and the ingest reported ``status: done``.

Three things had to be true of the code that produced it, and each is asserted
here separately:

* frame indices span the whole timeline (the arithmetic was always right),
* the describe pass has no silent cap and survives the free tier's rate limit,
* whatever is missing is reported, never rounded up to "complete".

No network: ``_gemini_describe``, ``sample_frames``, the store and the
transcriber are all monkeypatched, and pacing/backoff are zeroed so the suite
never sleeps. Nothing here touches ``apps/api/data/``.
"""

from __future__ import annotations

import logging

import pytest

from app.ingestion import audio as audio_mod
from app.ingestion import backoff as backoff_mod
from app.ingestion import embedder as embedder_mod
from app.ingestion import jobs as jobs_mod
from app.ingestion import visual as visual_mod
from app.ingestion.visual import (
    FRAME_INTERVAL_S,
    VisualPassReport,
    frame_bounds,
    iter_frame_indices,
)

DURATION_MS = 655_500
INTERVAL_MS = 4_000
#: ceil(655500 / 4000). This is the number the shipped cap prevented reaching.
N_FRAMES = 164


@pytest.fixture(autouse=True)
def _no_pacing(monkeypatch):
    """Zero the inter-request pacing and backoff so tests never sleep.

    The real values are load bearing in production but are a wall-clock cost
    only; :func:`test_describe_survives_a_rate_limit` asserts the retry
    behaviour itself with an injected ``sleep``.
    """
    monkeypatch.setattr(visual_mod, "DESCRIBE_MIN_INTERVAL_S", 0.0)
    monkeypatch.setattr(visual_mod, "DESCRIBE_BACKOFF_S", 0.0)
    monkeypatch.setattr(visual_mod, "DESCRIBE_BACKOFF_CAP_S", 0.0)


def _stub_sampler(monkeypatch, *, count: int, fail_from: int | None = None):
    """Replace PyAV with a clean, monotonic index walk of ``count`` frames."""

    def _sample(path, **kw):
        for index in range(count):
            if fail_from is not None and index >= fail_from:
                raise RuntimeError(f"decode error at frame {index}")
            yield index, b"jpeg"

    monkeypatch.setattr(visual_mod, "sample_frames", _sample)


def _stub_describer(monkeypatch, fn=None):
    def _default(frames):
        return {i: "a plotted curve over axes" for i, _ in frames}

    monkeypatch.setattr(visual_mod, "_gemini_describe", fn or _default)


class _FakeStore:
    def __init__(self):
        self.added: list = []

    def add(self, chunks):
        self.added.extend(chunks)
        return len(chunks)


def _wire_jobs(monkeypatch, *, duration_ms: int = DURATION_MS):
    """Point ``ingest_media`` at an in-memory store and a stubbed transcriber."""
    store = _FakeStore()
    monkeypatch.setattr(jobs_mod, "_store", lambda: store)
    monkeypatch.setattr(
        jobs_mod, "media_duration_ms", lambda path, **kw: duration_ms
    )
    monkeypatch.setattr(
        audio_mod, "transcribe", lambda *a, **kw: ([], {"engine": "stub"})
    )
    return store


# ---------------------------------------------------------------------------
# 1. The arithmetic: indices must span the whole duration
# ---------------------------------------------------------------------------


def test_frame_indices_span_the_whole_duration():
    """164 indices for a 655.5s file at 4s, reaching past 9/10 of the runtime.

    The shipped cap of 40 meant only indices 0..39 were ever requested, so the
    walk stopped at 160000ms -- 24.4% of the file. Asserting the *count* and
    the *last start* together makes both halves of that regression executable.
    """
    indices = iter_frame_indices(DURATION_MS, interval_s=FRAME_INTERVAL_S)

    assert len(indices) == N_FRAMES, (
        f"a {DURATION_MS}ms video at {FRAME_INTERVAL_S}s must yield {N_FRAMES} "
        f"frame indices, got {len(indices)}"
    )
    assert indices[0] == 0
    assert indices[-1] == 163
    assert indices == sorted(indices)

    starts = [frame_bounds(i, duration_ms=DURATION_MS)[0] for i in indices]
    assert starts[0] == 0
    assert max(starts) == 163 * INTERVAL_MS == 652_000
    assert max(starts) > 0.9 * DURATION_MS, (
        "the sampled walk must reach the last tenth of the video"
    )
    # The final partial interval is still covered, clamped to the real end.
    assert frame_bounds(indices[-1], duration_ms=DURATION_MS)[1] == DURATION_MS

    # The default is no cap, so the count is not 40 any more.
    assert len(indices) != 40


# ---------------------------------------------------------------------------
# 2. The end-to-end effect: the last visual chunk is near the end
# ---------------------------------------------------------------------------


def test_visual_chunks_reach_the_end_of_the_video(monkeypatch):
    """A full pass times a frame at 652s, not one at 156s."""
    _stub_sampler(monkeypatch, count=N_FRAMES)
    _stub_describer(monkeypatch)

    report = visual_mod.extract_visual_pass("fake.mp4", duration_ms=DURATION_MS)

    assert report.status == "complete"
    assert report.warnings == []
    assert len(report.covered_indices) == N_FRAMES

    last = report.segments[-1]
    assert last.frame_index == 163
    assert last.start_ms == 652_000
    assert last.end_ms == DURATION_MS
    assert last.start_ms > 0.9 * DURATION_MS, (
        f"last described frame starts at {last.start_ms}ms -- the tail of the "
        "video has no visual description again"
    )
    assert report.span_pct == 100.0
    assert report.coverage_pct == 100.0


def test_ingest_media_indexes_visual_chunks_through_to_the_end(monkeypatch):
    """End-to-end through the pipeline: no cap in the default path.

    This is the assertion that would have caught the shipped bug -- the chunk
    *list* the store receives, not just the sampler.
    """
    _stub_sampler(monkeypatch, count=N_FRAMES)
    _stub_describer(monkeypatch)
    store = _wire_jobs(monkeypatch)

    report = jobs_mod.ingest_media(
        "fake.mp4", course_id="coverageprobe", resource_id="res_probe"
    )

    assert report["visual_status"] == "complete"
    assert report["warnings"] == []
    assert report["visual_expected_frames"] == N_FRAMES
    assert report["visual_described_frames"] == N_FRAMES
    assert report["visual_span_pct"] == 100.0

    visual = [c for c in store.added if c.metadata.get("kind") == "visual"]
    assert len(visual) == N_FRAMES
    assert max(c.start_ms for c in visual) == 652_000
    assert max(c.end_ms for c in visual) == DURATION_MS


def test_the_removed_forty_frame_cap_reproduces_the_bug_and_is_now_degraded(
    monkeypatch,
):
    """``max_frames=40`` still works as an explicit opt-in -- and is labelled.

    It is kept as a knob, but it can no longer masquerade as a complete
    ingest. The 24.4% span is asserted because that is the exact number the
    shipped resource measured, and it is the whole reason this file exists.
    """
    _stub_sampler(monkeypatch, count=N_FRAMES)
    _stub_describer(monkeypatch)

    report = visual_mod.extract_visual_pass(
        "fake.mp4", duration_ms=DURATION_MS, max_frames=40
    )

    assert len(report.segments) == 40
    assert max(s.start_ms for s in report.segments) == 156_000
    assert report.span_pct == round(100 * 160_000 / DURATION_MS, 1) == 24.4
    assert report.status == "partial"
    assert report.truncated is True
    assert report.warnings, "a truncated pass must not report cleanly"


# ---------------------------------------------------------------------------
# 3. The actual regression: partial description is never "complete"
# ---------------------------------------------------------------------------


def test_partial_description_is_not_reported_as_complete(monkeypatch):
    """A describe failure part-way through must surface on the job record.

    The shipped code had two independent silences: a frame cap that stopped
    the walk, and a blanket ``except`` around the whole visual pass. Either one
    produced a clean ``status: done``. Both are now recorded.

    The job itself stays ``done`` because the audio is real, transcribed, and
    indexed -- failing the whole ingest over a missing modality would throw
    away good data and, with a four-state client contract, has nowhere honest
    to put "mostly worked". What must not happen is silence.
    """
    _stub_sampler(monkeypatch, count=N_FRAMES)

    def _rate_limited_then_ok(frames):
        if any(i >= 40 for i, _ in frames):
            raise RuntimeError("429 RESOURCE_EXHAUSTED: generate_content rate limit")
        return {i: "a plotted curve over axes" for i, _ in frames}

    _stub_describer(monkeypatch, _rate_limited_then_ok)
    store = _wire_jobs(monkeypatch)

    job = jobs_mod.new_job("coverageprobe", "res_probe")
    jobs_mod.run_ingest(
        job,
        lambda: jobs_mod.ingest_media(
            "fake.mp4", course_id="coverageprobe", resource_id="res_probe"
        ),
    )

    # Audio survives; the job is not pretended to have failed.
    assert job.status == "done"

    payload = jobs_mod.job_payload(job)
    assert payload["status"] == "done"
    assert payload["visual_status"] == "partial", (
        "a pass that described only part of the video must not read as complete"
    )
    assert payload["visual_coverage_pct"] == round(100 * 40 / N_FRAMES, 1)
    assert payload["warnings"], "the degradation must be stated, not implied"
    assert any("visual pass" in w for w in payload["warnings"])
    assert any("429" in w for w in payload["warnings"]), (
        "the underlying failure must be visible, not swallowed"
    )

    visual = [c for c in store.added if c.metadata.get("kind") == "visual"]
    assert len(visual) == 40
    assert max(c.start_ms for c in visual) == 156_000


def test_a_visual_pass_that_describes_nothing_is_reported_as_failed(monkeypatch):
    """Total loss of the visual modality is named, and still not a silent done."""
    _stub_sampler(monkeypatch, count=N_FRAMES)

    def _always_fails(frames):
        raise RuntimeError("429 RESOURCE_EXHAUSTED")

    _stub_describer(monkeypatch, _always_fails)
    _wire_jobs(monkeypatch)

    job = jobs_mod.new_job("coverageprobe", "res_probe")
    jobs_mod.run_ingest(
        job,
        lambda: jobs_mod.ingest_media(
            "fake.mp4", course_id="coverageprobe", resource_id="res_probe"
        ),
    )

    payload = jobs_mod.job_payload(job)
    assert payload["status"] == "done"  # audio is still real
    assert payload["visual_status"] == "failed"
    assert payload["visual_coverage_pct"] == 0.0
    assert payload["warnings"]


def test_describe_survives_a_rate_limit(monkeypatch):
    """A 429 is retried, and a temporary failure does not shorten the index.

    The free tier returns ``429 RESOURCE_EXHAUSTED`` well before 164 frames can
    be described in a burst. Retrying (rather than letting the exception end
    the walk) is what keeps the *tail* of a lecture from being the part that
    silently disappears.
    """
    _stub_sampler(monkeypatch, count=N_FRAMES)
    attempts = {"n": 0}

    def _flaky(frames):
        attempts["n"] += 1
        if attempts["n"] <= 3:
            raise RuntimeError("429 RESOURCE_EXHAUSTED: quota")
        return {i: "a plotted curve over axes" for i, _ in frames}

    _stub_describer(monkeypatch, _flaky)

    report = visual_mod.extract_visual_pass("fake.mp4", duration_ms=DURATION_MS)

    batches = -(-N_FRAMES // 8)
    assert attempts["n"] == batches + 3, (
        "each rate-limited batch must be retried, not abandoned -- "
        f"{batches} batches plus 3 retries expected, saw {attempts['n']}"
    )
    assert report.status == "complete"
    assert len(report.covered_indices) == N_FRAMES
    assert report.warnings == []


def test_an_unusable_model_response_is_retried_then_reported(monkeypatch):
    """Garbled output is not silently accepted as "described nothing".

    An empty parse and a 429 look the same to the caller, so both are retried;
    when retries run out the frames are named as a gap rather than dropped.
    """
    _stub_sampler(monkeypatch, count=N_FRAMES)
    monkeypatch.setattr(visual_mod, "_gemini_describe", lambda frames: {})

    report = visual_mod.extract_visual_pass("fake.mp4", duration_ms=DURATION_MS)

    assert report.segments == []
    assert report.status == "failed"
    assert report.warnings, "an all-empty pass must say so"


def test_describe_false_reports_skipped_not_failed(monkeypatch):
    """``describe=False`` attempted nothing, so it is not a failure."""

    def _explode(*a, **kw):
        raise AssertionError("describe=False must not sample frames")

    monkeypatch.setattr(visual_mod, "sample_frames", _explode)
    monkeypatch.setattr(
        visual_mod, "_gemini_describe", lambda frames: pytest.fail("no model call")
    )

    report = visual_mod.extract_visual_pass(
        "fake.mp4", duration_ms=DURATION_MS, describe=False
    )
    assert report.status == "skipped"
    assert report.segments == []


# ---------------------------------------------------------------------------
# 4. Timestamps are index arithmetic, and the duration guard still holds
# ---------------------------------------------------------------------------


def test_timestamps_come_from_index_arithmetic(monkeypatch):
    """A model that narrates timestamps cannot move a single bound.

    The fake descriptor embeds a different (and wrong) time in every frame --
    ``index * 999`` ms, plus a timecode string -- and out-of-order indices. The
    segments must still be timed purely as ``index * interval_ms``.
    """
    _stub_sampler(monkeypatch, count=N_FRAMES)

    def _hallucinating(frames):
        return {
            i: f"at {i // 60:02d}:{i % 60:02d} (really {i * 999}s) a red curve"
            for i, _ in reversed(frames)
        }

    _stub_describer(monkeypatch, _hallucinating)

    report = visual_mod.extract_visual_pass("fake.mp4", duration_ms=DURATION_MS)
    assert report.status == "complete"
    assert [s.frame_index for s in report.segments] == list(range(N_FRAMES))

    for seg in report.segments:
        assert seg.start_ms == seg.frame_index * INTERVAL_MS
        assert seg.end_ms == min((seg.frame_index + 1) * INTERVAL_MS, DURATION_MS)
        assert seg.end_ms <= DURATION_MS
        if seg.frame_index:
            # The model's own claim for this frame is 999ms per index. That is
            # never what the segment says -- index 0 is excluded only because
            # both arithmetic paths happen to agree at zero.
            assert seg.start_ms != seg.frame_index * 999


def test_a_model_timeline_overshooting_the_duration_is_still_rejected():
    """Requirement 4: the duration guard was not weakened to accommodate the fix.

    The audio-side reconciliation is the one that *rejects* rather than clamps.
    Asserted here so a future change cannot quietly relax the tolerance while
    fixing something unrelated.
    """
    assert not audio_mod.reconcile_gemini_timeline(
        [audio_mod.TranscriptSegment(0.0, 900.0, "hallucinated tail")], 655.5
    ).accepted
    assert audio_mod.GEMINI_TIMELINE_OVERSHOOT_TOLERANCE_S == 0.05


def test_visual_bounds_survive_a_hallucinated_out_of_grid_index(monkeypatch):
    """Even a frame index past the end cannot push a chunk past the duration.

    The model may return any integer it likes; the arithmetic clamps it down,
    and the pipeline's final clamp is asserted as the belt-and-braces backstop.
    """
    _stub_sampler(monkeypatch, count=N_FRAMES)
    _stub_describer(
        monkeypatch,
        lambda frames: {i: f"frame {i}" for i, _ in frames}
        | {99_999: "a frame from nowhere"},
    )
    store = _wire_jobs(monkeypatch)

    jobs_mod.ingest_media(
        "fake.mp4", course_id="coverageprobe", resource_id="res_probe"
    )

    visual = [c for c in store.added if c.metadata.get("kind") == "visual"]
    assert visual, "the real frames must still be indexed"
    for chunk in visual:
        assert chunk.start_ms is not None and chunk.end_ms is not None
        assert chunk.end_ms <= DURATION_MS
        assert chunk.end_ms > chunk.start_ms


# ---------------------------------------------------------------------------
# 4b. The model may NAME a frame. It may not DECIDE where it is.
#
# `test_timestamps_come_from_index_arithmetic` above only proves that a
# fabricated timecode *inside description text* is ignored. It stubs
# `_gemini_describe`, so the `index` field never passes through
# `_parse_frame_descriptions` at all -- the channel by which model output can
# actually set a timestamp was never asserted. That is why this shipped.
# ---------------------------------------------------------------------------


def _reply(frames: list[tuple[int, bytes]]) -> str:
    """A describe reply carrying exactly the indices it was sent."""
    import json

    return json.dumps(
        {
            "frames": [
                {"index": i, "description": f"frame {i}: a plotted curve"}
                for i, _ in frames
            ]
        }
    )


def _pass_raw_reply(monkeypatch, raw_for):
    """Drive the real parser with a canned model reply, sampler indices intact.

    Only ``google.genai`` is stubbed. Everything from the JSON text through
    ``_parse_frame_descriptions`` and ``_flush`` to ``build_visual_segments`` is
    the production code path -- which is the point: the index channel has to be
    exercised end to end, not simulated away by stubbing the describer.
    """
    import sys
    import types

    sent: list = []

    class _Models:
        def generate_content(self, model, contents):
            frames = _last_batch(contents)
            sent.append([i for i, _ in frames])
            return types.SimpleNamespace(text=raw_for(frames))

    class _Client:
        models = _Models()

    genai = types.ModuleType("google.genai")
    genai.Client = lambda api_key=None: _Client()
    pkg = types.ModuleType("google")
    pkg.genai = genai
    monkeypatch.setitem(sys.modules, "google", pkg)
    monkeypatch.setitem(sys.modules, "google.genai", genai)
    return sent


def _last_batch(contents) -> list[tuple[int, bytes]]:
    """Recover ``(index, jpeg)`` pairs from the prompt parts the module builds."""
    batch: list[tuple[int, bytes]] = []
    current: int | None = None
    for part in contents:
        if isinstance(part, str) and part.startswith("Frame index "):
            current = int(part.removeprefix("Frame index ").rstrip(":"))
        elif isinstance(part, dict):
            batch.append((current, part["inline_data"]["data"]))
    return batch


def test_model_supplied_index_cannot_set_timestamps(monkeypatch):
    """The core guard: a fabricated index cannot place a chunk on the timeline.

    The reply below is the shape that actually shipped: the model returns a
    correct description for every frame it was sent, but for one frame also
    volunteers a wildly out-of-range index (500, 999). Under the old parser
    those became real chunks at ``500 * 4000ms`` and ``999 * 4000ms``.

    Clamping alone would not be a fix -- it files the description against a
    timestamp nobody observed, which is the spoiler. The batch is refused and
    the frames are recorded as a gap.
    """
    _stub_sampler(monkeypatch, count=N_FRAMES)

    def _fabricate(frames):
        import json

        return json.dumps(
            {
                "frames": [
                    {"index": i, "description": f"frame {i}: a plotted curve"}
                    for i, _ in frames
                ]
                + [
                    {"index": 500, "description": "a stray frame from nowhere"},
                    {"index": 999, "description": "another stray frame"},
                ]
            }
        )

    _pass_raw_reply(monkeypatch, _fabricate)
    store = _wire_jobs(monkeypatch)

    jobs_mod.ingest_media("fake.mp4", course_id="coverageprobe", resource_id="res_probe")

    visual = [c for c in store.added if c.metadata.get("kind") == "visual"]
    assert visual, "the real frames must still be indexed"

    # No chunk may sit at a fabricated index's timestamp.
    fabricated_starts = {500 * INTERVAL_MS, 999 * INTERVAL_MS}
    assert not (fabricated_starts & {c.start_ms for c in visual})

    for chunk in visual:
        assert chunk.start_ms % INTERVAL_MS == 0, (
            f"chunk starts at {chunk.start_ms}ms, which is not a sampled frame"
        )
        assert chunk.start_ms // INTERVAL_MS < N_FRAMES
        assert chunk.end_ms <= DURATION_MS

    # And the mismatch is recorded rather than silently absorbed. The stray
    # indices belong to no sampled frame, so the pass stays `complete` over the
    # 164 real frames while the drop is still visible in `failures`.
    report = visual_mod.extract_visual_pass("fake.mp4", duration_ms=DURATION_MS)
    assert report.status == "complete"
    assert len(report.segments) == N_FRAMES
    assert report.failures, "a dropped description must be recorded"
    assert any("not sampled" in f for f in report.failures)


def test_unknown_index_is_dropped_not_reassigned(monkeypatch):
    """One bogus index must not cost the valid frames beside it, or be guessed at.

    Both halves matter. Discarding the whole reply loses real content; keeping
    the stray entry and clamping it down to the end of the video files a
    description against a time it was never seen at.
    """
    _stub_sampler(monkeypatch, count=N_FRAMES)

    def _one_bad(frames):
        import json

        return json.dumps(
            {
                "frames": [
                    {"index": i, "description": f"frame {i}: a plotted curve"}
                    for i, _ in frames
                ]
                + [{"index": 12_345, "description": "invented"}]
            }
        )

    _pass_raw_reply(monkeypatch, _one_bad)

    report = visual_mod.extract_visual_pass("fake.mp4", duration_ms=DURATION_MS)

    assert [s.frame_index for s in report.segments] == list(range(N_FRAMES)), (
        "the valid indices in the reply must be unaffected"
    )
    assert 12_345 not in {s.frame_index for s in report.segments}
    # No segment is clamped down onto the last frame to absorb the stray one.
    assert len([s for s in report.segments if s.end_ms == DURATION_MS]) == 1
    assert report.failures, "a dropped description must be recorded"


def test_duplicate_index_does_not_duplicate_a_chunk(monkeypatch):
    """A model that labels two images with one index has lost track of them.

    Last-write-wins would silently attribute one image's description to the
    other and hide the fact that the reply is untrustworthy, so the repeat is
    a mismatch.
    """
    _stub_sampler(monkeypatch, count=N_FRAMES)

    def _repeat(frames):
        import json

        return json.dumps(
            {
                "frames": [
                    {"index": i, "description": f"frame {i}: a plotted curve"}
                    for i, _ in frames
                ]
                + [{"index": 3, "description": "a second, different frame 3"}]
            }
        )

    _pass_raw_reply(monkeypatch, _repeat)
    store = _wire_jobs(monkeypatch)

    jobs_mod.ingest_media("fake.mp4", course_id="coverageprobe", resource_id="res_probe")

    visual = [c for c in store.added if c.metadata.get("kind") == "visual"]
    starts = [c.start_ms for c in visual]
    assert len(starts) == len(set(starts)), "one chunk per sampled frame, no repeats"
    assert not any("a second, different frame 3" in c.text for c in visual)


def test_no_visual_chunk_exceeds_the_duration(monkeypatch):
    """For a 655500ms file, no stored chunk may claim a moment past its end."""
    _stub_sampler(monkeypatch, count=N_FRAMES)
    _pass_raw_reply(monkeypatch, _reply)
    store = _wire_jobs(monkeypatch)

    jobs_mod.ingest_media("fake.mp4", course_id="coverageprobe", resource_id="res_probe")

    visual = [c for c in store.added if c.metadata.get("kind") == "visual"]
    assert visual
    assert max(c.end_ms for c in visual) <= DURATION_MS


def test_visual_chunk_count_equals_sampler_index_count(monkeypatch):
    """164 segments for a 655500ms file at 4s -- never 174, never 165.

    The count is asserted against ``iter_frame_indices``, not a literal, so it
    keeps meaning "one per sampled frame" if the interval ever changes.
    """
    _stub_sampler(monkeypatch, count=N_FRAMES)
    _pass_raw_reply(monkeypatch, _reply)

    report = visual_mod.extract_visual_pass("fake.mp4", duration_ms=DURATION_MS)

    expected = iter_frame_indices(DURATION_MS, interval_s=FRAME_INTERVAL_S)
    assert len(expected) == N_FRAMES
    assert len(report.segments) == len(expected) == N_FRAMES
    assert [s.frame_index for s in report.segments] == expected
    assert [s.start_ms for s in report.segments] == [
        i * INTERVAL_MS for i in expected
    ]


def test_final_partial_interval_is_clamped(monkeypatch):
    """The last frame's tail is 3500ms of a 4000ms interval, ending at duration.

    ``iter_frame_indices`` samples the final partial interval on purpose -- a
    655.5s file has 3.5s of content nobody would otherwise describe -- so that
    tail must close exactly on the authoritative duration.
    """
    _stub_sampler(monkeypatch, count=N_FRAMES)
    _pass_raw_reply(monkeypatch, _reply)

    report = visual_mod.extract_visual_pass("fake.mp4", duration_ms=DURATION_MS)

    last = report.segments[-1]
    assert last.frame_index == 163
    assert last.start_ms == 163 * INTERVAL_MS == 652_000
    assert last.end_ms == DURATION_MS
    assert last.end_ms - last.start_ms == 3_500 < INTERVAL_MS

    # The clamp holds for indices past the end too -- used to be the leak.
    assert frame_bounds(164, duration_ms=DURATION_MS)[1] == DURATION_MS
    assert frame_bounds(99_999, duration_ms=DURATION_MS)[1] == DURATION_MS


def test_the_index_channel_is_actually_validated():
    """Why the previous guard missed this: the stub never reached the parser.

    ``test_timestamps_come_from_index_arithmetic`` replaces
    ``_gemini_describe``, so its indices are the real ones by construction and
    the trust boundary is never crossed. :func:`_validated_descriptions` is that
    boundary, and until now no test fed it a model-chosen index at all.
    """
    validate = visual_mod._validated_descriptions

    # A reply whose indices all name sampled frames passes through untouched.
    kept, notes = validate({4: "a curve", 5: "another"}, {4, 5})
    assert kept == {4: "a curve", 5: "another"}
    assert notes == []

    # An index naming no sampled frame yields NO entry...
    kept, notes = validate({4: "a curve", 500: "invented"}, {4, 5})
    assert kept == {4: "a curve"}
    assert notes and "[500]" in notes[0]

    # ...and it is not clamped onto the last sampled frame instead.
    kept, _ = validate({4: "a curve", 500: "invented"}, {4, 5})
    assert 5 not in kept
    assert all(i in {4, 5} for i in kept)

    # A repeated index keeps only the first occurrence, never the last.
    parsed = visual_mod._parse_frame_descriptions(
        '{"frames": [{"index": 4, "description": "first"}, '
        '{"index": 4, "description": "second"}]}'
    )
    assert parsed == {4: "first"}


def test_a_valid_batch_keeps_its_index_channel_intact(monkeypatch):
    """The model still gets told which frame it is looking at.

    Dropping the index from the prompt would be the wrong fix: the model needs
    it to know *which* of eight images it is describing. What must not happen
    is that index being trusted for storage.
    """
    _stub_sampler(monkeypatch, count=N_FRAMES)
    sent = _pass_raw_reply(monkeypatch, _reply)

    report = visual_mod.extract_visual_pass("fake.mp4", duration_ms=DURATION_MS)

    assert report.status == "complete"
    assert report.warnings == []
    assert sent[0] == list(range(8)), (
        "the prompt must still carry the sampled index for each frame"
    )
    assert len(report.segments) == N_FRAMES


def test_build_visual_segments_drops_an_unsampled_index():
    """The storage-layer guard, independent of how the description arrived."""
    segments = visual_mod.build_visual_segments(
        {0: "a curve", 5: "another curve", 500: "a frame from nowhere"},
        duration_ms=DURATION_MS,
        sampled_indices=list(range(N_FRAMES)),
    )

    assert [s.frame_index for s in segments] == [0, 5]
    assert all(s.end_ms <= DURATION_MS for s in segments)


# ---------------------------------------------------------------------------
# 5. Report arithmetic, isolated from the pipeline
# ---------------------------------------------------------------------------


def test_report_distinguishes_coverage_from_span():
    """Covering the first quarter plus the last frame is neither 25% nor 100%.

    Quoting span alone would have let the shipped bug read as "100% covered --
    the end is described". The report carries both numbers so neither
    understatement nor overstatement is available.
    """
    report = VisualPassReport(
        segments=[
            visual_mod.VisualSegment(i, "curve", *frame_bounds(i, duration_ms=DURATION_MS))
            for i in [*range(40), 163]
        ],
        expected_indices=list(range(N_FRAMES)),
        sampled_indices=list(range(N_FRAMES)),
        described_indices=[*range(40), 163],
        duration_ms=DURATION_MS,
    )

    assert report.status == "partial"
    assert report.coverage_pct == round(100 * 41 / N_FRAMES, 1)
    assert report.span_pct == 100.0, "span alone would have hidden the hole"
    assert report.warnings


# ---------------------------------------------------------------------------
# 6. The embedding quota -- the other half of the free-tier problem
# ---------------------------------------------------------------------------


def test_a_rate_limited_embedding_batch_is_retried(monkeypatch):
    """A 429 on the *embedding* quota must not throw away a finished ingest.

    Found live: the re-ingest described all 164 frames, then died at the chunk
    write on ``embed_content_free_tier_requests`` (100/min). The pass had cost
    six minutes of transcription and description, and an unretried 429 at the
    last step discarded all of it. Same failure class as the describe cap: a
    rate limit ending a pass part-way.
    """
    calls = {"n": 0}

    class _Models:
        def embed_content(self, model, contents):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("429 RESOURCE_EXHAUSTED: embed_content")
            return type(
                "R",
                (),
                {"embeddings": [type("E", (), {"values": [0.1]})() for _ in contents]},
            )()

    emb = embedder_mod.GeminiEmbedder.__new__(embedder_mod.GeminiEmbedder)
    emb._client = type("C", (), {"models": _Models()})()
    emb._model = "gemini-embedding-2"
    emb.dim = embedder_mod.GEMINI_EMBED_DIM

    vectors = backoff_mod.call_with_retry(
        lambda: emb._embed_batch(["a", "b"]),
        what="test",
        sleep=lambda _s: None,
    )

    assert calls["n"] == 2, "the embedding batch must be retried"
    assert len(vectors) == 2


def test_embedding_backoff_spans_the_quota_window_it_is_retried_for():
    """The embedder's delays must actually straddle a per-minute quota refill.

    ``base ** attempt`` would send a 15s base straight past the 60s cap on the
    first retry; the geometric form gives 15, 30, 60, which is what a 100/min
    quota asking for ~23s needs. Both constants are asserted so neither can be
    tuned down to make an ingest "finish faster".
    """
    delays = [
        backoff_mod.backoff_delay(
            n, base=embedder_mod.GeminiEmbedder.EMBED_BACKOFF_S,
            cap=embedder_mod.GeminiEmbedder.EMBED_BACKOFF_CAP_S,
        )
        for n in range(1, embedder_mod.GeminiEmbedder.EMBED_MAX_ATTEMPTS + 1)
    ]
    assert delays[:3] == [15.0, 30.0, 60.0]
    assert max(delays) == 60.0, "the ceiling must hold however many attempts run"
    assert sum(delays) >= 300, (
        "the full retry budget must cover a whole quota window -- ~265 per-chunk "
        "requests against a 100/min cap needs minutes, not seconds"
    )

    # The describe loop keeps the short schedule.
    assert backoff_mod.backoff_delay(1) == 2.0
    assert backoff_mod.backoff_delay(4) == 16.0


def test_a_permanent_embedding_failure_still_raises(monkeypatch):
    """Retry must be bounded: a bad key must not loop forever."""
    attempts = {"n": 0}

    class _Models:
        def embed_content(self, model, contents):
            attempts["n"] += 1
            raise ValueError("API key not valid")

    emb = embedder_mod.GeminiEmbedder.__new__(embedder_mod.GeminiEmbedder)
    emb._client = type("C", (), {"models": _Models()})()
    emb._model = "gemini-embedding-2"
    emb.dim = embedder_mod.GEMINI_EMBED_DIM

    with pytest.raises(ValueError, match="API key not valid"):
        backoff_mod.call_with_retry(
            lambda: emb._embed_batch(["a"]), what="test", sleep=lambda _s: None
        )
    assert attempts["n"] == 1, "a non-retryable error must not be retried at all"


def test_a_degraded_pass_is_logged_at_error(monkeypatch, caplog):
    """The degradation must reach the log even if nobody reads the job record."""
    _stub_sampler(monkeypatch, count=N_FRAMES)
    _stub_describer(monkeypatch, lambda frames: {0: "one frame only"})

    with caplog.at_level(logging.ERROR):
        report = visual_mod.extract_visual_pass("fake.mp4", duration_ms=DURATION_MS)

    assert report.status == "partial"
    assert caplog.records, "a partial visual pass must be logged"
    assert "DEGRADED" in caplog.text or "visual pass" in caplog.text