"""Retrieval tests: scoping, timestamp gating, RRF, end-to-end (no DB/network)."""

from __future__ import annotations

import asyncio

import pytest

from app.ingestion.embedder import HashingEmbedder
from app.ingestion.models import Chunk, text_hash
from app.retrieval.filters import assert_no_leakage, build_scope_filter
from app.retrieval.hybrid import HybridRetriever, reciprocal_rank_fusion
from app.retrieval.service import RetrievalService
from app.retrieval.store import InMemoryVectorStore, PostgresVectorStore, ScoredChunk

T = 60_000  # learner timestamp: 60s
EMBEDDER = HashingEmbedder()


def make_chunk(
    cid: str,
    resource_id: str,
    source_type: str,
    text: str,
    start_ms: int | None = None,
    end_ms: int | None = None,
    page_no: int | None = None,
    slide_no: int | None = None,
    permitted: bool = True,
    course_id: str = "course1",
) -> Chunk:
    return Chunk(
        id=cid,
        resource_id=resource_id,
        course_id=course_id,
        source_type=source_type,
        chunk_index=0,
        text=text,
        text_hash=text_hash(text),
        embed_model=EMBEDDER.name,
        video_id="vid1" if source_type in ("video", "video_transcript") else None,
        start_ms=start_ms,
        end_ms=end_ms,
        page_no=page_no,
        slide_no=slide_no,
        permitted=permitted,
        embedding=EMBEDDER.embed(text),
    )


# ---------------------------------------------------------------------------
# (a) SQL filter: Postgres store encodes course + scope + T in the query text.
# ---------------------------------------------------------------------------


def test_postgres_search_sql_enforces_scope_and_timestamp():
    store = PostgresVectorStore(
        database_url="postgresql+asyncpg://localhost:5432/unused"
    )
    sql, params = store.build_search_sql("course1", ["res1", "res2"], T, k=8)
    assert "course_id" in sql
    assert "resource_id" in sql
    assert "video_transcript" in sql
    assert "end_ms" in sql
    assert "end_ms IS NOT NULL" in sql
    assert "end_ms <= :current_time_ms" in sql
    assert "permitted" in sql
    assert "<=>" in sql
    assert "LIMIT" in sql
    assert params["course_id"] == "course1"
    assert params["current_time_ms"] == T
    assert set(params.values()) >= {"res1", "res2"}


def test_postgres_search_empty_scope_returns_nothing_without_db():
    store = PostgresVectorStore(
        database_url="postgresql+asyncpg://localhost:5432/unused"
    )
    sql, _ = store.build_search_sql("course1", [], T)
    assert "1 = 0" in sql  # empty scope matches nothing, stays valid SQL
    assert store._engine is None  # building SQL never connects
    hits = asyncio.run(store.search("course1", [], T, [0.0] * 1536, k=8))
    assert hits == []
    assert store._engine is None  # empty scope short-circuits before connecting


def test_build_scope_filter_is_pure_and_canonical():
    f = build_scope_filter("c1", ["b", "a", "a"], 1234)
    assert f == {
        "course_id": "c1",
        "resource_ids": ["a", "b"],
        "current_time_ms": 1234,
        "video_cutoff_ms": 1234,
        "video_source_types": ["video", "video_transcript"],
        "permitted_only": True,
    }


# ---------------------------------------------------------------------------
# (b) Leakage gate: post-T transcript dropped; PDF/PPT kept regardless of T.
# ---------------------------------------------------------------------------


def test_assert_no_leakage_drops_post_t_transcript_keeps_docs():
    early = make_chunk("early", "vid", "video_transcript", "intro concepts", 5_000, 9_000)
    late = make_chunk(
        "late", "vid", "video_transcript", "spoiler finale reveal", 600_000, 605_000
    )
    pdf = make_chunk(
        "pdf1", "doc", "pdf", "reference appendix", start_ms=600_000, page_no=9
    )
    ppt = make_chunk("ppt1", "deck", "pptx", "slide deck summary", slide_no=3)
    kept, violations = assert_no_leakage([early, late, pdf, ppt], T, ["vid", "doc", "deck"])
    assert [c.id for c in kept] == ["early", "pdf1", "ppt1"]
    assert len(violations) == 1
    assert violations[0].chunk_id == "late"
    assert violations[0].reason == "timestamp"


def test_assert_no_leakage_preserves_scored_chunk_type_and_order():
    ok = ScoredChunk(chunk=make_chunk("ok", "vid", "video", "hello", 1_000, 2_000), score=0.9)
    bad = ScoredChunk(
        chunk=make_chunk("bad", "vid", "video", "hello", 999_000, 999_500), score=0.99
    )
    kept, violations = assert_no_leakage([ok, bad], T, ["vid"])
    assert kept == [ok]
    assert all(type(v) is ScoredChunk for v in kept)  # input items kept as-is
    assert [v.chunk_id for v in violations] == ["bad"]


def test_assert_no_leakage_strict_drops_overlapping_chunk():
    # Strict gate: start_ms <= T is NOT enough; end_ms must also be <= T.
    overlap = make_chunk("overlap", "vid", "video", "straddles pause", 50_000, 70_000)
    assert overlap.start_ms <= T < overlap.end_ms
    kept, violations = assert_no_leakage([overlap], T, ["vid"])
    assert kept == []
    assert [v.chunk_id for v in violations] == ["overlap"]
    assert violations[0].reason == "timestamp"


def test_assert_no_leakage_keeps_chunk_ending_exactly_at_t():
    edge = make_chunk("edge", "vid", "video_transcript", "ends at pause", 50_000, T)
    kept, violations = assert_no_leakage([edge], T, ["vid"])
    assert [c.id for c in kept] == ["edge"]
    assert violations == []


def test_assert_no_leakage_drops_null_end_ms_video_keeps_docs():
    null_video = make_chunk("nullvid", "vid", "video", "unbounded span", 1_000, None)
    null_pdf = make_chunk("nullpdf", "doc", "pdf", "reference notes", page_no=1)
    kept, violations = assert_no_leakage([null_video, null_pdf], T, ["vid", "doc"])
    assert [c.id for c in kept] == ["nullpdf"]
    assert [v.chunk_id for v in violations] == ["nullvid"]
    assert violations[0].reason == "timestamp"


# ---------------------------------------------------------------------------
# (c) Scope gate: chunks outside allowed_resource_ids are dropped.
# ---------------------------------------------------------------------------


def test_assert_no_leakage_drops_out_of_scope_chunks():
    mine = make_chunk("mine", "res1", "pdf", "allowed content", page_no=1)
    other = make_chunk("theirs", "res9", "pdf", "forbidden content", page_no=1)
    other_video = make_chunk("ov", "res9", "video_transcript", "early but scoped out", 1_000)
    kept, violations = assert_no_leakage([mine, other, other_video], T, ["res1"])
    assert [c.id for c in kept] == ["mine"]
    assert {v.chunk_id for v in violations} == {"theirs", "ov"}
    assert all(v.reason == "scope" for v in violations)


def test_inmemory_store_enforces_scope_timestamp_permitted_and_course():
    chunks = [
        make_chunk("keep", "r1", "video_transcript", "alpha beta", 10_000, 15_000),
        make_chunk("future", "r1", "video_transcript", "alpha beta", 500_000, 505_000),
        make_chunk("scoped-out", "rX", "pdf", "alpha beta", page_no=1),
        make_chunk("locked", "r1", "pdf", "alpha beta", page_no=2, permitted=False),
        make_chunk("wrong-course", "r1", "pdf", "alpha beta", page_no=3, course_id="c2"),
    ]
    store = InMemoryVectorStore(chunks)
    q = EMBEDDER.embed("alpha beta")
    hits = asyncio.run(store.search("course1", ["r1"], T, q, k=10))
    assert [h.chunk.id for h in hits] == ["keep"]
    assert asyncio.run(store.search("course1", [], T, q)) == []


def test_inmemory_store_strict_gate_drops_overlap_null_keeps_edge():
    overlap = make_chunk("overlap", "r1", "video", "alpha beta", 50_000, 70_000)
    edge = make_chunk("edge", "r1", "video", "alpha beta", 50_000, T)
    null_end = make_chunk("nullend", "r1", "video_transcript", "alpha beta", 1_000, None)
    store = InMemoryVectorStore([overlap, edge, null_end])
    q = EMBEDDER.embed("alpha beta")
    hits = asyncio.run(store.search("course1", ["r1"], T, q, k=10))
    assert [h.chunk.id for h in hits] == ["edge"]


# ---------------------------------------------------------------------------
# (d) Hybrid RRF ordering is sane on synthetic scores.
# ---------------------------------------------------------------------------


def test_reciprocal_rank_fusion_prefers_consensus():
    fused = reciprocal_rank_fusion([["a", "b"], ["b", "c"]], rrf_k=60)
    assert fused["b"] == pytest.approx(1 / 61 + 1 / 62)
    assert fused["a"] == pytest.approx(1 / 61)
    assert fused["c"] == pytest.approx(1 / 62)
    ranked = sorted(fused, key=fused.__getitem__, reverse=True)
    assert ranked == ["b", "a", "c"]
    assert reciprocal_rank_fusion([]) == {}


class _StubStore:
    def __init__(self, hits: list[ScoredChunk]) -> None:
        self._hits = hits
        self.calls: list = []

    async def search(self, course_id, allowed, t_ms, qvec, k=8):
        self.calls.append((course_id, list(allowed), t_ms, k))
        return list(self._hits)


class _StubFTS:
    def __init__(self, hits) -> None:
        self._hits = hits

    def search_text(self, question, course_id, allowed, t_ms, k):
        return list(self._hits)


def _scored(cid: str, score: float, resource_id: str = "r1") -> ScoredChunk:
    return ScoredChunk(chunk=make_chunk(cid, resource_id, "pdf", f"text {cid}"), score=score)


def test_hybrid_vector_only_path_preserves_score_order_and_top_k():
    store = _StubStore([_scored("a", 0.9), _scored("b", 0.5), _scored("c", 0.7)])
    retriever = HybridRetriever(store=store, embedder=EMBEDDER)
    hits = asyncio.run(retriever.retrieve("q", "course1", ["r1"], T, k=2))
    assert [h.chunk.id for h in hits] == ["a", "c"]


def test_hybrid_rrf_consensus_beats_single_list_leader():
    store = _StubStore([_scored("x", 0.99), _scored("y", 0.5)])
    fts = _StubFTS([_scored("y", 0.1), _scored("z", 0.0)])
    retriever = HybridRetriever(store=store, embedder=EMBEDDER, fts=fts)
    hits = asyncio.run(retriever.retrieve("q", "course1", ["r1"], T, k=3))
    # y ranks in both lists -> highest RRF; x leads vector-only but ranks once
    assert [h.chunk.id for h in hits] == ["y", "x", "z"]


def test_hybrid_filters_fts_hits_before_fusion():
    store = _StubStore([_scored("ok", 0.4)])
    leaked = make_chunk("leaked", "r1", "video_transcript", "spoiler", 999_000, 999_500)
    fts = _StubFTS([ScoredChunk(chunk=leaked, score=99.0)])
    retriever = HybridRetriever(store=store, embedder=EMBEDDER, fts=fts)
    hits = asyncio.run(retriever.retrieve("q", "course1", ["r1"], T, k=5))
    assert [h.chunk.id for h in hits] == ["ok"]


# ---------------------------------------------------------------------------
# (e) Service end-to-end: post-T chunk never returned, provenance attached.
# ---------------------------------------------------------------------------


def _fixture_store() -> InMemoryVectorStore:
    early = make_chunk(
        "c_early",
        "vid",
        "video_transcript",
        "photosynthesis converts light energy into chemical energy",
        5_000,
        12_000,
    )
    # Adversarial: text identical to the query (cosine = 1.0) yet after T.
    late = make_chunk(
        "c_late",
        "vid",
        "video_transcript",
        "what is photosynthesis light chemical energy",
        600_000,
        610_000,
    )
    pdf = make_chunk(
        "c_pdf",
        "handout",
        "pdf",
        "photosynthesis light chemical energy chapter notes",
        page_no=2,
    )
    return InMemoryVectorStore([early, late, pdf])


def test_service_end_to_end_excludes_post_t_chunk():
    service = RetrievalService(store=_fixture_store(), embedder=EMBEDDER)
    passages = asyncio.run(
        service.retrieve(
            "what is photosynthesis light chemical energy",
            "course1",
            ["vid", "handout"],
            T,
            k=8,
        )
    )
    ids = [p.chunk_id for p in passages]
    assert "c_late" not in ids
    assert set(ids) == {"c_early", "c_pdf"}
    by_id = {p.chunk_id: p for p in passages}
    assert by_id["c_early"].source_type == "video_transcript"
    assert by_id["c_early"].start_ms == 5_000
    assert by_id["c_early"].end_ms == 12_000
    assert by_id["c_pdf"].page_no == 2
    assert all(p.text and p.score > 0 for p in passages)


def test_service_empty_scope_or_question_returns_nothing():
    service = RetrievalService(store=_fixture_store(), embedder=EMBEDDER)
    assert asyncio.run(service.retrieve("photosynthesis", "course1", [], T)) == []
    assert (
        asyncio.run(service.retrieve("   ", "course1", ["vid", "handout"], T)) == []
    )
