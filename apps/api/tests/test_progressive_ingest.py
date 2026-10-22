"""Progressive (windowed) ingest: stream/full modes, progress, /position.

Fakes for transcribe / visual / duration; real Chroma store in ``tmp_path``.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app.chat.llm import LLMResult
from app.chat.prompts import REFUSAL_TEXT
from app.chat.service import ChatService
from app.chat.sessions import InMemoryMessageStore, InMemorySessionStore
from app.ingestion import audio as audio_mod
from app.ingestion import jobs as jobs_mod
from app.ingestion import visual as visual_mod
from app.subjects import store as subjects_store
from tests.store_helpers import make_embedder

COURSE = "course_1"
RID = "res_vid"
DUR = 420_000  # 7 minutes -> 4 windows: 120/120/120/60 s

GATE = {"event": None}  # optional blocker for the transcribe fake
CALLS: list[tuple[int, int]] = []


def _fake_transcribe(path, *, duration_ms=None, allow_gemini=True, start_ms=None, end_ms=None):
    s, e = start_ms or 0, end_ms if end_ms is not None else duration_ms
    CALLS.append((s, e))
    if GATE["event"] is not None:
        GATE["event"].wait(10)
    segs = []
    t = s
    while t < e:
        # 40 s segments on a 30 s step: the last one overruns the window end.
        segs.append(
            audio_mod.TranscriptSegment(
                start_s=t / 1000, end_s=(t + 40_000) / 1000,
                text=f"partial derivatives lecture at {t}",
            )
        )
        t += 30_000
    return segs, {"timestamp_agreement": 1.0}


def _fake_visual(path, *, duration_ms, start_ms=None, end_ms=None, **kw):
    idx = visual_mod.window_frame_indices(duration_ms, start_ms=start_ms, end_ms=end_ms)
    segs = [
        visual_mod.VisualSegment(
            frame_index=i, text=f"slide shows gradient frame {i}",
            start_ms=i * 4000, end_ms=min(i * 4000 + 4000, duration_ms),
        )
        for i in idx
    ]
    return visual_mod.VisualPassReport(
        segments=segs, expected_indices=idx, duration_ms=duration_ms
    )


@pytest.fixture
def env(tmp_path, monkeypatch):
    from app.ingestion import router as ingestion_router
    from app.main import app
    from app.retrieval import registry
    from app.retrieval.store import ChromaVectorStore

    monkeypatch.setattr(subjects_store, "SUBJECT_STATE_PATH", tmp_path / "s.json")
    subjects_store.set_persistence_enabled(False)
    subjects_store.reset_store()
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    monkeypatch.setattr(ingestion_router, "UPLOAD_DIR", uploads)
    monkeypatch.setattr(jobs_mod, "media_duration_ms", lambda p: DUR)
    monkeypatch.setattr(audio_mod, "transcribe", _fake_transcribe)
    monkeypatch.setattr(visual_mod, "extract_visual_pass", _fake_visual)
    CALLS.clear()
    GATE["event"] = None
    jobs_mod.clear_jobs()

    store = ChromaVectorStore(
        path=tmp_path / "chroma", embedder=make_embedder(), collection="test_prog"
    )
    previous = registry.get_shared_store()
    registry.set_shared_store(store)
    try:
        with TestClient(app) as client:
            yield client, store, uploads, ingestion_router
    finally:
        GATE["event"] = None
        registry.set_shared_store(previous)
        subjects_store.reset_store()
        jobs_mod.clear_jobs()


def _run(store, **kw):
    job = jobs_mod.new_job(COURSE, RID)
    jobs_mod.run_ingest(
        job,
        lambda: jobs_mod.ingest_media_windows(
            "fake.mp4", course_id=COURSE, resource_id=RID, title="lec.mp4", **kw
        ),
    )
    return job


def _chunks(store):
    got = store._collection.get(include=["metadatas"])
    return got["ids"], got["metadatas"]


def _wait(client, job_id, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        body = client.get(f"/ingestion/jobs/{job_id}").json()
        if body["status"] in ("done", "failed"):
            return body
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_window_bounds_cover_exactly():
    b = jobs_mod.window_bounds(DUR)
    assert b == [(0, 120_000), (120_000, 240_000), (240_000, 360_000), (360_000, 420_000)]
    assert jobs_mod.WINDOW_MS % 4000 == 0


def test_full_mode_covers_all_windows_once(env):
    _, store, _, _ = env
    job = _run(store, mode="full")
    assert job.status == "done", job.error
    assert CALLS == [(0, 120_000), (120_000, 240_000), (240_000, 360_000), (360_000, 420_000)]
    p = jobs_mod.job_payload(job)
    assert p["mode"] == "full"
    assert p["processed_through_ms"] == p["duration_ms"] == DUR
    assert p["windows_done"] == p["windows_total"] == 4
    assert {"job_id", "resource_id", "status"} <= set(p)
    ids, _ = _chunks(store)
    assert len(ids) == len(set(ids))
    r = next(x for x in store.resources(COURSE) if x["id"] == RID)
    assert r["status"] == "ready" and r["processed_through_ms"] == DUR


def test_stream_mode_stops_after_initial_windows(env):
    _, store, _, _ = env
    job = _run(store, mode="stream")
    assert job.status == "done"
    p = jobs_mod.job_payload(job)
    assert p["processed_through_ms"] == 360_000
    assert p["windows_done"] == p["windows_total"] == 3
    assert len(CALLS) == 3
    r = next(x for x in store.resources(COURSE) if x["id"] == RID)
    assert r["status"] == "partial" and r["processed_through_ms"] == 360_000
    fr = store.processing_frontier(RID)
    assert fr["processed_through_ms"] == 360_000 and fr["duration_ms"] == DUR


def test_chunks_bounded_by_window_and_rerun_has_no_stale(env):
    _, store, _, _ = env
    _run(store, mode="full")
    ids1, metas1 = _chunks(store)
    for m in metas1:
        assert m["end_ms"] <= m["processed_through_ms"]
        assert m["end_ms"] <= DUR
    # Re-run window 1 only: same count, same ids, nothing stale.
    _run(store, mode="stream", start_window=1, n_windows=1)
    ids2, metas2 = _chunks(store)
    assert sorted(ids1) == sorted(ids2)
    # Window 1 chunks never leak past 240s, and no duplicates exist.
    win1 = [m for m in metas2 if 120_000 <= m["start_ms"] < 240_000]
    assert win1 and all(m["end_ms"] <= 240_000 for m in win1)


def test_mode_validation_422(env):
    client, *_ = env
    r = client.post(
        f"/courses/{COURSE}/ingest",
        data={"mode": "bogus"},
        files={"file": ("a.mp4", b"\x00" * 16, "video/mp4")},
    )
    assert r.status_code == 422


def test_upload_stream_mode_attaches_after_first_window(env, monkeypatch):
    client, _store, _uploads, router_mod = env
    monkeypatch.setattr(router_mod, "schedule", lambda job, work: work())
    sub = client.post("/subjects", json={"name": "S", "course_id": COURSE}).json()
    attached_at: list[int] = []
    orig = router_mod._attach_to_subject

    def spy(subject, rid):
        # Record the committed transcript frontier at attach time.
        attached_at.append(_store.processing_frontier(rid)["processed_through_ms"])
        return orig(subject, rid)

    monkeypatch.setattr(router_mod, "_attach_to_subject", spy)
    r = client.post(
        f"/courses/{COURSE}/ingest",
        data={"mode": "stream", "subject_id": sub["id"]},
        files={"file": ("lec.mp4", b"\x00" * 16, "video/mp4")},
    )
    assert r.status_code == 200, r.text
    # Attached after the first stage T commit and before the second window's
    # commit (commits are serialised). CALLS is not used: with two lanes the next
    # window's transcribe may already be running when the callback fires.
    assert attached_at[0] == 120_000
    rid = r.json()["resource_id"]
    listing = client.get(f"/subjects/{sub['id']}/resources").json()["resources"]
    row = next(x for x in listing if x["id"] == rid)
    assert row["status"] == "partial" and row["processed_through_ms"] == 360_000
    course_row = next(
        x for x in client.get(f"/courses/{COURSE}/resources").json()["resources"]
        if x["id"] == rid
    )
    assert course_row["status"] == "partial"


def _seed_partial(client, store, uploads, frontier_windows=3):
    (uploads / f"{RID}.mp4").write_bytes(b"x")
    _run(store, mode="stream", n_windows=frontier_windows)
    jobs_mod.clear_jobs()  # simulate a restart: only the store remembers


def test_position_queued_then_idempotent_then_ready(env):
    client, store, uploads, _ = env
    _seed_partial(client, store, uploads)
    CALLS.clear()
    GATE["event"] = threading.Event()
    url = f"/courses/{COURSE}/resources/{RID}/position"
    r1 = client.post(url, json={"t_ms": 300_000})
    assert r1.status_code == 200, r1.text
    b1 = r1.json()
    assert b1["status"] == "queued" and b1["job_id"]
    assert b1["processed_through_ms"] == 360_000 and b1["duration_ms"] == DUR
    r2 = client.post(url, json={"t_ms": 300_000}).json()
    assert r2["status"] == "running"
    GATE["event"].set()
    done = _wait(client, b1["job_id"])
    assert done["status"] == "done"
    assert CALLS == [(360_000, 420_000)]  # exactly one window job
    # Transcript is complete but visuals lag: not ready yet, a V catch-up queues.
    r3 = client.post(url, json={"t_ms": 300_000}).json()
    assert r3["status"] == "queued" and r3["processed_through_ms"] == DUR
    assert r3["visual_through_ms"] == 360_000
    _wait(client, r3["job_id"])
    r4 = client.post(url, json={"t_ms": 300_000}).json()
    assert r4["status"] == "ready" and r4["visual_through_ms"] == DUR


def test_position_far_idle_and_validation(env):
    client, store, uploads, _ = env
    _seed_partial(client, store, uploads, frontier_windows=3)
    url = f"/courses/{COURSE}/resources/{RID}/position"
    r = client.post(url, json={"t_ms": 0}).json()
    assert r["status"] == "idle" and r["processed_through_ms"] == 360_000
    assert client.post(url, json={"t_ms": -1}).status_code == 422
    assert client.post(
        f"/courses/other/resources/{RID}/position", json={"t_ms": 0}
    ).status_code == 404
    assert client.post(
        f"/courses/{COURSE}/resources/res_nope/position", json={"t_ms": 0}
    ).status_code == 404


def test_position_without_asset_is_409(env):
    client, store, uploads, _ = env
    _seed_partial(client, store, uploads)
    (uploads / f"{RID}.mp4").unlink()
    r = client.post(
        f"/courses/{COURSE}/resources/{RID}/position", json={"t_ms": 350_000}
    )
    assert r.status_code == 409


class _LLM:
    async def complete(self, *, system, user_prompt, passages, mode) -> LLMResult:
        if passages:
            return LLMResult(
                text=f"Answer [chunk_id={passages[0].chunk_id}]",
                cited_ids=[passages[0].chunk_id],
            )
        return LLMResult(text=REFUSAL_TEXT, cited_ids=[])


def _chat(store, t_ms):
    sessions = InMemorySessionStore()
    svc = ChatService(
        store=store, embedder=make_embedder(), llm=_LLM(), sessions=sessions,
        messages=InMemoryMessageStore(),
    )
    s = sessions.create(
        learner_id="l1", course_id=COURSE, resource_id=RID, lesson_id=None,
        resource_scope=[RID], current_time_ms=t_ms,
    )
    return asyncio.run(
        svc.chat(
            session_id=s.id, current_time_ms=t_ms,
            message="what are partial derivatives?", focus_resource_id=RID,
        )
    )


def test_chat_pending_past_frontier_and_strict_gate_before(env):
    _, store, _, _ = env
    _run(store, mode="stream")
    past = _chat(store, 400_000)
    assert past.processing_pending is True
    assert past.processed_through_ms == 360_000
    ok = _chat(store, 200_000)
    assert ok.processing_pending is False and ok.grounded
    assert all(c.end_ms <= 200_000 for c in ok.citations)


def test_pipelined_lanes_keep_the_job_and_trace_context(env):
    """Both lanes run on pool threads; their steps must reach the job's trace run."""
    from app import devtrace

    _, store, _, _ = env
    devtrace.clear()
    job = _run(store, mode="full")
    assert job.status == "done", job.error
    mine = [e for e in devtrace.events_after(0, limit=5000) if e["run_id"] == job.job_id]
    steps = {e["step"] for e in mine}
    assert {"transcribe", "describe", "chunk", "embed", "index"} <= steps
    assert mine[0]["step"] == "request" and mine[-1]["step"] == "respond"
