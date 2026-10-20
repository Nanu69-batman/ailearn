# ruff: noqa: F811
"""Two-stage (transcript first, visuals after) ingest. Fakes only."""

from __future__ import annotations

import asyncio
import re
import threading

from app.chat.llm import LLMResult
from app.chat.prompts import REFUSAL_TEXT
from app.chat.service import VISUAL_PENDING_REFUSAL_TEXT, ChatService
from app.chat.sessions import InMemoryMessageStore, InMemorySessionStore
from app.ingestion import audio as audio_mod
from app.ingestion import jobs as jobs_mod
from app.ingestion import visual as visual_mod
from tests.store_helpers import make_chunk, make_embedder
from tests.test_progressive_ingest import (  # noqa: F401  (env is a fixture)
    _LLM,
    CALLS,
    COURSE,
    DUR,
    GATE,
    RID,
    _chunks,
    _run,
    _wait,
    env,
)

T_ONLY = ("transcript",)


def _chat(store, t_ms, llm=None):
    sessions = InMemorySessionStore()
    svc = ChatService(
        store=store, embedder=make_embedder(), llm=llm or _LLM(), sessions=sessions,
        messages=InMemoryMessageStore(),
    )
    calls = []

    async def spy(**kw):
        calls.append(kw)

    svc._supplemental_answer = spy
    s = sessions.create(
        learner_id="l1", course_id=COURSE, resource_id=RID, lesson_id=None,
        resource_scope=[RID], current_time_ms=t_ms,
    )
    resp = asyncio.run(
        svc.chat(session_id=s.id, current_time_ms=t_ms,
                 message="what are partial derivatives?", focus_resource_id=RID)
    )
    return resp, calls


class _RefusingLLM:
    async def complete(self, *, system, user_prompt, passages, mode) -> LLMResult:
        return LLMResult(text=REFUSAL_TEXT, cited_ids=[])


def test_transcript_queryable_before_visuals(env):
    _, store, _, _ = env
    job = _run(store, mode="stream", n_windows=2, stages=T_ONLY)
    assert job.status == "done", job.error
    _, metas = _chunks(store)
    assert metas and all(m["kind"] == "transcript" for m in metas)
    resp, _ = _chat(store, 100_000)
    assert not resp.refused and resp.grounded
    assert resp.visual_pending is True and resp.visual_through_ms == 0
    assert all(c.end_ms <= 100_000 for c in resp.citations)  # strict gate intact
    # Visual catch-up lands: no longer pending.
    _run(store, mode="stream", n_windows=2, stages=("visual",))
    resp, _ = _chat(store, 100_000)
    assert resp.visual_pending is False


def test_visual_pending_refusal_is_distinct_with_no_supplemental(env):
    _, store, _, _ = env
    _run(store, mode="stream", n_windows=2, stages=T_ONLY)
    resp, supp = _chat(store, 100_000, llm=_RefusingLLM())
    assert resp.refused and resp.visual_pending
    assert resp.answer == VISUAL_PENDING_REFUSAL_TEXT != REFUSAL_TEXT
    assert supp == []  # no supplemental / general-knowledge fallback


def test_legacy_resource_never_visual_pending(env):
    _, store, _, _ = env
    store.add([make_chunk("legacy_1", resource_id=RID, course_id=COURSE,
                          text="partial derivatives", start_ms=0, end_ms=60_000)])
    resp, _ = _chat(store, 30_000)
    assert resp.visual_pending is False and resp.visual_through_ms is None
    resp, supp = _chat(store, 30_000, llm=_RefusingLLM())
    assert resp.answer == REFUSAL_TEXT and resp.visual_pending is False
    assert len(supp) >= 1  # legacy refusal keeps the normal supplemental path


def test_no_visual_resource_not_pending(env):
    _, store, _, _ = env
    _run(store, mode="full", with_visual=False)
    fr = store.processing_frontier(RID)
    assert fr["visual_through_ms"] == DUR
    resp, _ = _chat(store, 200_000)
    assert resp.visual_pending is False


def _snapshot(store):
    got = store._collection.get(include=["metadatas", "documents"])
    return sorted(
        (i, d, tuple(sorted((k, v) for k, v in m.items())))
        for i, d, m in zip(got["ids"], got["documents"], got["metadatas"])
    )


def test_pipelined_equals_serial(env):
    _, store, _, _ = env
    assert _run(store, mode="full", pipelined=True).status == "done"
    piped = _snapshot(store)
    store.delete_resource(RID)
    assert _run(store, mode="full", pipelined=False).status == "done"
    assert _snapshot(store) == piped and piped


def test_progress_keys_and_done_only_when_both_lanes_finish(env):
    _, store, _, _ = env
    job = _run(store, mode="full")
    p = jobs_mod.job_payload(job)
    assert job.status == "done"
    for k in ("processed_through_ms", "visual_through_ms", "windows_done",
              "transcript_windows_done", "visual_windows_done", "windows_total"):
        assert k in p, k
    assert p["transcript_windows_done"] == p["visual_windows_done"] == 4
    assert p["processed_through_ms"] == p["visual_through_ms"] == DUR


def test_vision_lane_exception_fails_job(env, monkeypatch):
    _, store, _, _ = env

    def boom(*a, **k):
        raise RuntimeError("vision exploded")

    monkeypatch.setattr(visual_mod, "extract_visual_pass", boom)
    job = _run(store, mode="full")
    assert job.status == "failed"
    assert "visual" in job.error and "vision exploded" in job.error


def test_transcript_lane_exception_fails_job(env, monkeypatch):
    _, store, _, _ = env
    orig = audio_mod.transcribe

    def boom(path, *, start_ms=None, **k):
        if start_ms == 120_000:
            raise RuntimeError("whisper exploded")
        return orig(path, start_ms=start_ms, **k)

    monkeypatch.setattr(audio_mod, "transcribe", boom)
    job = _run(store, mode="full")
    assert job.status == "failed"
    assert "transcript" in job.error and "whisper exploded" in job.error


def test_position_queues_two_windows_plus_visual_catchup_idempotent_ready(env):
    client, store, uploads, router_mod = env
    router_mod.reset_inflight()
    (uploads / f"{RID}.mp4").write_bytes(b"x")
    _run(store, mode="stream", n_windows=1, stages=T_ONLY)  # T=120k, V=0
    jobs_mod.clear_jobs()
    CALLS.clear()
    GATE["event"] = threading.Event()
    url = f"/courses/{COURSE}/resources/{RID}/position"
    b1 = client.post(url, json={"t_ms": 100_000}).json()
    assert b1["status"] == "queued" and len(b1["job_ids"]) == 2
    assert b1["job_id"] == b1["job_ids"][0]
    assert b1["processed_through_ms"] == 120_000 and b1["visual_through_ms"] == 0
    b2 = client.post(url, json={"t_ms": 100_000}).json()
    # The (ungated) visual job may already be done; the gated T job is in flight.
    assert b2["status"] == "running" and b1["job_ids"][0] in b2["job_ids"]
    assert set(b2["job_ids"]) <= set(b1["job_ids"])  # nothing new queued
    GATE["event"].set()
    for jid in b1["job_ids"]:
        assert _wait(client, jid)["status"] == "done"
    assert sorted(CALLS) == [(120_000, 240_000), (240_000, 360_000)]  # 2 T windows, once
    fr = store.processing_frontier(RID)
    assert fr["processed_through_ms"] == 360_000 and fr["visual_through_ms"] == 120_000
    # Not ready until BOTH frontiers reach the duration.
    for _ in range(6):
        b = client.post(url, json={"t_ms": 300_000}).json()
        if b["status"] == "ready":
            break
        assert b["status"] in ("queued", "running", "idle")
        for jid in b["job_ids"]:
            _wait(client, jid)
    assert b["status"] == "ready"
    assert b["processed_through_ms"] == b["visual_through_ms"] == DUR
    assert b["job_ids"] == []


def test_delete_range_by_kind_only_removes_that_stage(env):
    _, store, _, _ = env
    _run(store, mode="full")
    store.add([make_chunk("other_c0_x", resource_id=RID + "_2", course_id=COURSE,
                          start_ms=0, end_ms=60_000)])

    def count(kind):
        _, metas = _chunks(store)
        return sum(1 for m in metas if m["resource_id"] == RID and m["kind"] == kind
                   and m["end_ms"] <= 120_000)

    t0, v0 = count("transcript"), count("visual")
    assert t0 and v0
    assert store.delete_resource_range(RID, 0, 120_000, kind="visual") == v0
    assert count("visual") == 0 and count("transcript") == t0
    assert store.delete_resource_range(RID, 0, 120_000, kind="transcript") == t0
    assert count("transcript") == 0
    _, metas = _chunks(store)
    assert any(m["resource_id"] == RID + "_2" for m in metas)  # exact id only
    assert any(m["resource_id"] == RID and m["end_ms"] > 120_000 for m in metas)


def test_chunk_ids_disjoint_per_stage(env):
    _, store, _, _ = env
    _run(store, mode="full")
    ids, metas = _chunks(store)
    assert len(ids) == len(set(ids))
    ords = {"transcript": set(), "visual": set()}
    for i, m in zip(ids, metas):
        ords[m["kind"]].add(int(re.search(r"_c(\d+)_", i).group(1)))
    assert ords["transcript"] and ords["visual"]
    assert not (ords["transcript"] & ords["visual"])
    span = jobs_mod.STAGE_ORDINAL_SPAN
    for o in ords["visual"]:
        assert (o % jobs_mod.WINDOW_MS) >= span
    for o in ords["transcript"]:
        assert (o % jobs_mod.WINDOW_MS) < span
