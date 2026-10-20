# ruff: noqa: F811
"""A window with nothing to index still advances the frontier. Fakes only."""

from __future__ import annotations

import threading

from app.ingestion import audio as audio_mod
from app.ingestion import visual as visual_mod
from tests.test_progressive_ingest import (  # noqa: F401  (env is a fixture)
    COURSE,
    GATE,
    RID,
    _chunks,
    _fake_transcribe,
    _fake_visual,
    _run,
    env,
)

SILENT_START = 120_000


def _silent_second_window(path, **kw):
    if kw.get("start_ms") == SILENT_START:
        return [], {}
    return _fake_transcribe(path, **kw)


def _blank_second_window(path, **kw):
    report = _fake_visual(path, **kw)
    if kw.get("start_ms") == SILENT_START:
        report.segments = []
    return report


def test_empty_windows_advance_both_frontiers(env, monkeypatch):
    client, store, uploads, router_mod = env
    monkeypatch.setattr(audio_mod, "transcribe", _silent_second_window)
    monkeypatch.setattr(visual_mod, "extract_visual_pass", _blank_second_window)
    job = _run(store, mode="stream", n_windows=2)
    assert job.status == "done", job.error

    info = store.processing_frontier(RID)
    assert info["processed_through_ms"] == 240_000
    assert info["visual_through_ms"] == 240_000

    # The heartbeat moves on to window 2 instead of re-queueing window 1.
    router_mod.reset_inflight()
    (uploads / f"{RID}.mp4").write_bytes(b"x")
    GATE["event"] = threading.Event()  # hold the queued job so _INFLIGHT stays
    try:
        b = client.post(
            f"/courses/{COURSE}/resources/{RID}/position", json={"t_ms": 200_000}
        ).json()
        assert b["processed_through_ms"] == 240_000
        assert b["status"] == "queued"
        keys = [k for k in router_mod._INFLIGHT if k[1] == "transcript"]
        assert keys and all(w >= 240_000 for _r, _k, w in keys)
    finally:
        GATE["event"].set()


def test_progress_marker_is_never_retrieved(env, monkeypatch):
    store = env[1]
    monkeypatch.setattr(audio_mod, "transcribe", _silent_second_window)
    monkeypatch.setattr(visual_mod, "extract_visual_pass", _blank_second_window)
    _run(store, mode="stream", n_windows=2)

    _ids, metas = _chunks(store)
    markers = [m for m in metas if m.get("kind") == "progress_marker"]
    assert len(markers) == 2  # one per stage of the silent window
    hits = store.query(
        "anything", course_id=COURSE, resource_scope=[RID],
        current_time_ms=10**9, k=50,
    )
    assert hits and all(h.kind != "progress_marker" for h in hits)
    assert all(h.chunk_id for h in hits)

    # Purge still removes markers: exact resource_id equality.
    store.delete_resource(RID)
    assert store.processing_frontier(RID) is None
