"""Tests for the offline eval harness (app.eval.harness).

The end-to-end test runs the real Chroma store with the offline
HashingEmbedder over docs/eval/golden.jsonl. No network.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.eval import harness
from app.eval.fixture import ALLOWED_RESOURCES, RES_PDF, RES_VIDEO, build_fixture_chunks
from app.eval.harness import (
    GOLDEN_DEFAULT,
    ItemResult,
    apply_strict_cutoff,
    citation_supported,
    compute_scoreboard,
    detect_leakage,
    resolve_gold_chunk_ids,
    run_eval,
    should_refuse,
)


def _passage(chunk_id, source_type="video", end_ms=1000, score=0.5):
    return SimpleNamespace(
        chunk_id=chunk_id, source_type=source_type, end_ms=end_ms, score=score
    )


def test_detect_leakage_flags_future_null_and_scope():
    resource_of = {"a": RES_VIDEO, "b": RES_VIDEO, "c": RES_VIDEO, "d": "res_other"}
    retrieved = [
        _passage("a", end_ms=1000),
        _passage("b", end_ms=1001),
        _passage("c", end_ms=None),
        _passage("d", end_ms=10),
    ]
    findings = detect_leakage(retrieved, 1000, ALLOWED_RESOURCES, resource_of)
    by_id = {f.chunk_id: f.reason for f in findings}
    assert by_id == {"b": "timestamp", "c": "timestamp", "d": "scope"}


def test_detect_leakage_boundary_end_equal_t_is_allowed():
    assert detect_leakage([_passage("a", end_ms=500)], 500, ALLOWED_RESOURCES) == []


def test_apply_strict_cutoff_uses_end_ms_not_start_ms():
    chunks = build_fixture_chunks()
    t = 450_000  # inside video segment 300k-600k
    visible = apply_strict_cutoff(chunks, t, ALLOWED_RESOURCES)
    video = [c for c in visible if c.source_type == "video"]
    assert video and all(c.end_ms <= t for c in video)
    assert not any(c.start_ms == 300_000 for c in video)
    # Non-video sources are unaffected by T.
    assert any(c.source_type == "pdf" for c in visible)


def test_apply_strict_cutoff_drops_null_end_and_out_of_scope():
    null_end = SimpleNamespace(
        resource_id=RES_VIDEO, source_type="video", end_ms=None
    )
    foreign = SimpleNamespace(resource_id="nope", source_type="pdf", end_ms=None)
    assert apply_strict_cutoff([null_end, foreign], 10**9, ALLOWED_RESOURCES) == []


def test_resolve_gold_chunk_ids_video_pdf_ppt():
    chunks = build_fixture_chunks()
    by_id = {c.chunk_id: c for c in chunks}
    item = {
        "gold_sources": [
            {"kind": "video", "start_ms": 0, "end_ms": 300_000},
            {"kind": "pdf", "page": 2},
            {"kind": "ppt", "slide": 3},
        ]
    }
    ids = resolve_gold_chunk_ids(item, chunks)
    assert len(ids) == 3
    got = {by_id[i].source_type for i in ids}
    assert got == {"video", "pdf", "pptx"}
    assert resolve_gold_chunk_ids({"gold_sources": []}, chunks) == []


def test_citation_supported():
    assert citation_supported("chlorophyll absorbs light", "chlorophyll absorbs red light")
    assert not citation_supported("chlorophyll absorbs light", "unrelated words entirely")
    assert not citation_supported("", "text")
    assert not citation_supported("text", "")


def test_should_refuse_rules():
    assert should_refuse([], ["g"])
    assert should_refuse([_passage("x")], ["g"])  # gold not retrieved
    assert should_refuse([_passage("x")], [])  # unanswerable probe
    assert should_refuse([_passage("g", score=0.01)], ["g"], threshold=0.08)
    assert not should_refuse([_passage("g", score=0.5)], ["g"], threshold=0.08)


def test_compute_scoreboard():
    results = [
        ItemResult(id="1", expected="answer", decision="answer",
                   retrieved_gold_ids=["g"], latency_ms=1.0, citation_ok=True),
        ItemResult(id="2", expected="answer", decision="refuse", latency_ms=3.0,
                   citation_ok=False),
        ItemResult(id="3", expected="refuse", decision="refuse", latency_ms=2.0),
        ItemResult(id="4", expected="refuse", decision="answer", latency_ms=2.0,
                   leakage=[harness.LeakageFinding("c", "timestamp")]),
    ]
    sb = compute_scoreboard(results)
    assert sb["n_total"] == 4 and sb["n_answer"] == 2 and sb["n_refuse"] == 2
    assert sb["retrieval_acc"] == 0.5
    assert sb["refusal_correctness"] == 0.5
    assert sb["leakage_rate"] == 0.25
    assert sb["citation_acc"] == 0.5
    assert sb["latency_ms_p50"] == 2.0
    assert compute_scoreboard([])["leakage_rate"] == 0.0


def test_fixture_chunks_have_unique_ids_and_end_ms():
    chunks = build_fixture_chunks()
    assert len({c.chunk_id for c in chunks}) == len(chunks)
    assert all(c.end_ms is not None for c in chunks if c.source_type == "video")
    assert {c.resource_id for c in chunks} == set(ALLOWED_RESOURCES)
    assert RES_PDF in {c.resource_id for c in chunks}


@pytest.fixture(scope="module")
def scoreboard():
    return run_eval(GOLDEN_DEFAULT)


def test_run_eval_end_to_end_chroma_no_leakage(scoreboard):
    assert scoreboard["n_total"] == 25
    assert scoreboard["gap_mode"] == "chroma"
    assert scoreboard["leakage_rate"] == 0
    assert scoreboard["production_gap_rate"] == 0
    assert scoreboard["production_gap_items"] == 0
    assert all(not item["leakage"] for item in scoreboard["items"])
    assert scoreboard["retrieval_acc"] > 0.5
