"""Unit tests for the offline eval harness (helpers + scoreboard math)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.eval.fixture import (
    ALLOWED_RESOURCES,
    FIXTURE_COURSE_ID,
    build_fixture_chunks,
    fixture_resource_rows,
    to_vtt_text,
    write_fixture_vtt,
)
from app.eval.harness import (
    ItemResult,
    apply_strict_cutoff,
    citation_supported,
    compute_scoreboard,
    detect_leakage,
    load_golden,
    resolve_gold_chunk_ids,
    run_eval,
    should_refuse,
)
from app.ingestion.embedder import HashingEmbedder
from app.ingestion.jobs import run_ingestion
from app.ingestion.models import Chunk, text_hash
from app.retrieval.service import RetrievedPassage

GOLDEN = Path(__file__).resolve().parents[3] / "docs" / "eval" / "golden.jsonl"
T = 600_000
EMBEDDER = HashingEmbedder()


def _passage(
    cid: str,
    source_type: str = "video",
    start_ms: int | None = 0,
    end_ms: int | None = 300_000,
    score: float = 0.5,
) -> RetrievedPassage:
    return RetrievedPassage(
        chunk_id=cid,
        source_type=source_type,
        text="sample passage text",
        score=score,
        start_ms=start_ms,
        end_ms=end_ms,
    )


def _chunk(cid: str, **kw) -> Chunk:
    text = kw.pop("text", f"text for {cid}")
    return Chunk(
        id=cid,
        resource_id=kw.pop("resource_id", "res_video"),
        course_id=kw.pop("course_id", FIXTURE_COURSE_ID),
        source_type=kw.pop("source_type", "video"),
        chunk_index=0,
        text=text,
        text_hash=text_hash(text),
        embed_model=EMBEDDER.name,
        start_ms=kw.pop("start_ms", 0),
        end_ms=kw.pop("end_ms", 300_000),
        page_no=kw.pop("page_no", None),
        slide_no=kw.pop("slide_no", None),
        embedding=EMBEDDER.embed(text),
    )


# --- leakage detector --------------------------------------------------------


def test_leakage_detector_flags_post_t_video_chunk():
    late = _passage("late", "video", 600_000, 900_000)
    findings = detect_leakage([late], T, ["res_video"], {"late": "res_video"})
    assert len(findings) == 1
    assert findings[0].reason == "timestamp"


def test_leakage_detector_allows_boundary_and_docs():
    edge = _passage("edge", "video", 300_000, 600_000)  # end_ms == T: allowed
    pdf = _passage("pdf1", "pdf", None, None)  # docs unaffected by T
    ppt = _passage("ppt1", "pptx", None, None)
    findings = detect_leakage(
        [edge, pdf, ppt], T, ["res_video", "res_pdf", "res_ppt"],
        {"edge": "res_video", "pdf1": "res_pdf", "ppt1": "res_ppt"},
    )
    assert findings == []


def test_leakage_detector_flags_out_of_scope():
    mine = _passage("mine", "pdf", None, None)
    theirs = _passage("theirs", "pdf", None, None)
    findings = detect_leakage(
        [mine, theirs], T, ["res_a"], {"mine": "res_a", "theirs": "res_b"}
    )
    assert [f.chunk_id for f in findings] == ["theirs"]
    assert findings[0].reason == "scope"


def test_leakage_detector_flags_null_end_ms_video_chunk():
    null_video = _passage("nullvid", "video", 0, None)
    null_transcript = _passage("nulltr", "video_transcript", 100_000, None)
    findings = detect_leakage(
        [null_video, null_transcript],
        T,
        ["res_video"],
        {"nullvid": "res_video", "nulltr": "res_video"},
    )
    assert len(findings) == 2
    assert all(f.reason == "timestamp" for f in findings)
    assert all(f.detail == "end_ms=NULL" for f in findings)
    # docs with NULL timestamps stay clean
    pdf_null = _passage("pdfnull", "pdf", None, None)
    assert (
        detect_leakage([pdf_null], T, ["res_pdf"], {"pdfnull": "res_pdf"}) == []
    )


# --- refusal logic ------------------------------------------------------------


def test_should_refuse_empty_retrieval():
    assert should_refuse([], ["gold1"]) is True


def test_should_refuse_when_gold_visible_and_relevant():
    hits = [_passage("gold1", score=0.4), _passage("other", score=0.1)]
    assert should_refuse(hits, ["gold1"]) is False


def test_should_refuse_when_gold_gated_out():
    hits = [_passage("other", score=0.5)]
    assert should_refuse(hits, ["gold_future"]) is True


def test_should_refuse_low_score_below_threshold():
    hits = [_passage("gold1", score=0.01)]
    assert should_refuse(hits, ["gold1"], threshold=0.08) is True


def test_should_refuse_unanswerable_always():
    hits = [_passage("something", "pdf", None, None, score=0.9)]
    assert should_refuse(hits, []) is True


# --- citation support ----------------------------------------------------------


def test_citation_supported_positive_and_negative():
    gold = "RuBisCO attaches carbon dioxide onto RuBP in the stroma"
    assert citation_supported(gold, "The enzyme RuBisCO grabs carbon dioxide onto RuBP") is True
    assert citation_supported(gold, "Mendel crossed pea plants in the garden") is False
    assert citation_supported("", "some text") is False


# --- strict cutoff (filter-before-rank) --------------------------------------------


def test_strict_cutoff_drops_straddling_video_keeps_docs_and_boundary():
    chunks = build_fixture_chunks()
    visible = apply_strict_cutoff(chunks, 600_000, ALLOWED_RESOURCES)
    by_start = {(c.start_ms, c.end_ms) for c in visible if c.source_type == "video"}
    assert (0, 300_000) in by_start and (300_000, 600_000) in by_start
    assert (600_000, 900_000) not in by_start  # starts at T: not yet watched
    assert len([c for c in visible if c.source_type == "pdf"]) == 6
    assert len([c for c in visible if c.source_type == "pptx"]) == 8
    assert apply_strict_cutoff(chunks, 0, ALLOWED_RESOURCES) == [
        c for c in chunks if c.source_type in ("pdf", "pptx")
    ]
    scoped_out = apply_strict_cutoff(chunks, 3_600_000, ["res_video"])
    assert {c.resource_id for c in scoped_out} == {"res_video"}


def test_strict_cutoff_drops_null_end_ms_video_keeps_docs():
    null_video = _chunk("nullvid", source_type="video", start_ms=0, end_ms=None)
    null_transcript = _chunk(
        "nulltr", source_type="video_transcript", start_ms=100_000, end_ms=None
    )
    pdf_null = _chunk("pdfnull", source_type="pdf", start_ms=None, end_ms=None)
    ppt_null = _chunk("pptnull", source_type="pptx", start_ms=None, end_ms=None)
    visible = apply_strict_cutoff(
        [null_video, null_transcript, pdf_null, ppt_null],
        T,
        ["res_video", "res_pdf", "res_ppt"],
    )
    visible_ids = {c.id for c in visible}
    assert "nullvid" not in visible_ids
    assert "nulltr" not in visible_ids
    assert "pdfnull" in visible_ids
    assert "pptnull" in visible_ids


def test_production_gate_matches_strict_rule_on_boundary_chunk():
    """Parity: the production store gate and the eval-strict rule agree.
    A chunk starting exactly at T but ending after it is excluded by the
    store, and the strict detector flags nothing in the store output."""
    from app.retrieval.store import InMemoryVectorStore

    chunks = build_fixture_chunks(EMBEDDER)
    store = InMemoryVectorStore(chunks)
    boundary = next(
        c for c in chunks if c.start_ms == 600_000 and c.source_type == "video"
    )
    raw = asyncio.run(
        store.search(
            FIXTURE_COURSE_ID, ALLOWED_RESOURCES, T, EMBEDDER.embed(boundary.text), k=5
        )
    )
    assert boundary.id not in [h.chunk.id for h in raw]  # strict gate excludes it
    from app.retrieval.service import to_passage

    assert (
        detect_leakage(
            [to_passage(h) for h in raw], T, ALLOWED_RESOURCES,
            {c.id: c.resource_id for c in chunks},
        )
        == []
    )


# --- gold resolution + fixture --------------------------------------------------


def test_resolve_gold_chunk_ids_against_fixture():
    chunks = build_fixture_chunks()
    assert len(chunks) == 12 + 6 + 8
    video_item = {
        "gold_sources": [{"kind": "video", "start_ms": 600_000, "end_ms": 900_000}]
    }
    pdf_item = {"gold_sources": [{"kind": "pdf", "page": 3}]}
    ppt_item = {"gold_sources": [{"kind": "ppt", "slide": 5}]}
    assert len(resolve_gold_chunk_ids(video_item, chunks)) == 1
    assert len(resolve_gold_chunk_ids(pdf_item, chunks)) == 1
    assert len(resolve_gold_chunk_ids(ppt_item, chunks)) == 1
    assert resolve_gold_chunk_ids({"gold_sources": []}, chunks) == []


def test_fixture_chunks_embedded_and_scoped():
    chunks = build_fixture_chunks(EMBEDDER)
    assert all(c.embedding is not None and len(c.embedding) == 1536 for c in chunks)
    assert {c.resource_id for c in chunks} == set(ALLOWED_RESOURCES)
    assert all(c.course_id == FIXTURE_COURSE_ID for c in chunks)


def test_fixture_vtt_dry_run_ingestion(tmp_path: Path):
    vtt = write_fixture_vtt(tmp_path / "fixture.vtt")
    assert "WEBVTT" in to_vtt_text()
    row = fixture_resource_rows()[0]
    chunks = asyncio.run(run_ingestion(row, vtt, embedder=EMBEDDER, database_url=""))
    assert chunks  # dry-run (no DB) still extracts, chunks, embeds
    assert all(c.embedding is not None and len(c.embedding) == 1536 for c in chunks)
    assert all(c.resource_id == row["id"] for c in chunks)


# --- scoreboard math --------------------------------------------------------------


def _result(rid: str, expected: str, **kw) -> ItemResult:
    return ItemResult(
        id=rid,
        expected=expected,
        decision=kw.pop("decision", expected),
        retrieved_gold_ids=kw.pop("retrieved_gold_ids", []),
        leakage=kw.pop("leakage", []),
        latency_ms=kw.pop("latency_ms", 1.0),
        citation_ok=kw.pop("citation_ok", None),
    )


def test_scoreboard_math():
    results = [
        _result("a1", "answer", retrieved_gold_ids=["g"], latency_ms=1.0, citation_ok=True),
        _result("a2", "answer", retrieved_gold_ids=[], latency_ms=3.0, citation_ok=False),
        _result("r1", "refuse", latency_ms=2.0),
        _result("r2", "refuse", decision="answer", latency_ms=4.0),
    ]
    board = compute_scoreboard(results)
    assert board["retrieval_acc"] == 0.5
    assert board["refusal_correctness"] == 0.5
    assert board["leakage_rate"] == 0.0
    assert board["latency_ms_p50"] == 2.5  # median of [1,2,3,4]
    assert board["citation_acc"] == 0.5


def test_scoreboard_counts_leakage():
    ok = _result("ok", "answer", retrieved_gold_ids=["g"])
    leaked = _passage("late", "video", 999_000, 999_500)
    bad = _result(
        "bad",
        "answer",
        retrieved_gold_ids=[],
        leakage=detect_leakage([leaked], T, ["res_video"], {"late": "res_video"}),
    )
    board = compute_scoreboard([ok, bad])
    assert board["leakage_rate"] == 0.5
    assert board["retrieval_acc"] == 0.5


def test_scoreboard_flags_null_end_ms_video_as_leakage():
    """Golden-style case: a NULL-end_ms video chunk in the retrieved set must
    surface as scoreboard leakage (leakage_rate > 0)."""
    ok = _result("ok", "answer", retrieved_gold_ids=["g"])
    null_passage = _passage("nullvid", "video", 0, None)
    findings = detect_leakage(
        [null_passage], T, ["res_video"], {"nullvid": "res_video"}
    )
    assert len(findings) == 1
    assert findings[0].reason == "timestamp"
    assert findings[0].detail == "end_ms=NULL"
    bad = _result("bad", "answer", retrieved_gold_ids=[], leakage=findings)
    board = compute_scoreboard([ok, bad])
    assert board["leakage_rate"] == 0.5
    assert board["leakage_rate"] > 0.0


# --- golden file + full offline run -------------------------------------------------


def test_golden_file_schema_and_distribution():
    items = load_golden(GOLDEN)
    assert len(items) == 25
    required = {"id", "pause_at_ms", "question", "type", "gold_answer", "gold_sources", "expected"}
    assert all(required <= set(it) for it in items)
    assert all(it["expected"] in ("answer", "refuse") for it in items)
    assert {it["id"] for it in items} == {it["id"] for it in items}  # unique below
    assert len({it["id"] for it in items}) == 25
    types = [it["type"] for it in items]
    assert types.count("video_before") == 6
    assert types.count("video_later") == 4
    assert types.count("pdf") == 4
    assert types.count("ppt") == 3
    assert types.count("unanswerable") == 3
    assert types.count("followup") == 3
    assert types.count("adversarial") == 2
    pauses = {it["pause_at_ms"] for it in items}
    assert 0 in pauses and 3_600_000 in pauses  # T=0 and T=end edges covered
    # every gold source resolves to a real fixture chunk
    chunks = build_fixture_chunks()
    for it in items:
        if it["gold_sources"]:
            assert resolve_gold_chunk_ids(it, chunks), f"{it['id']} has dangling gold source"


def test_harness_offline_run_has_zero_leakage():
    board = run_eval(GOLDEN, k=5)
    assert {"retrieval_acc", "refusal_correctness", "leakage_rate", "latency_ms_p50"} <= set(board)
    assert board["leakage_rate"] == 0.0
    assert board["refusal_correctness"] >= 0.95
    assert board["retrieval_acc"] >= 0.5
