"""Retrieval tests: scoping, timestamp gating, RRF, end-to-end (no DB/network)."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from app.ingestion.embedder import HashingEmbedder
from app.ingestion.models import Chunk, text_hash
from app.retrieval.filters import assert_no_leakage, build_scope_filter
from app.retrieval.hybrid import HybridRetriever, reciprocal_rank_fusion
from app.retrieval.service import FOCUS_SCORE_FLOOR, RetrievalService
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


# ---------------------------------------------------------------------------
# (f) Focus rank: guarantee the learner's active resource owns the top rank
#     without dropping cross-resource context, and without weakening the
#     strict T gate.
# ---------------------------------------------------------------------------

RES_PDF_F = "res_pdf_focus"
RES_VIDEO_F = "res_video_focus"
VIDEO_ID_F = "vid_partial_derivatives"


def _video_scored(cid: str, score: float, resource_id: str, video_id: str) -> ScoredChunk:
    chunk = make_chunk(
        cid,
        resource_id,
        "video",
        f"partial derivatives transcript {cid}",
        start_ms=1_000,
        end_ms=12_000,
    )
    chunk.video_id = video_id
    return ScoredChunk(chunk=chunk, score=score)


def _pdf_scored(cid: str, score: float, resource_id: str) -> ScoredChunk:
    return ScoredChunk(
        chunk=make_chunk(cid, resource_id, "pdf", f"front matter {cid}", page_no=1),
        score=score,
    )


# PDF front matter repeats the question phrase in its title, contents listing
# and section headings, so it outranks the transcript by a wide margin. The
# shipped 1.15x multiplier could not flip a gap this large (the reported bug);
# the floor rule is order-independent and does.
_FOCUS_SCORE_PDF = 3.0
_FOCUS_SCORE_VIDEO = 0.5
_FOCUS_ORDER = ["res_pdf_focus", "res_video_focus"]


def _focus_stub_store() -> _StubStore:
    return _StubStore(
        [
            _pdf_scored("d_pdf", _FOCUS_SCORE_PDF, RES_PDF_F),
            _video_scored("c_vid", _FOCUS_SCORE_VIDEO, RES_VIDEO_F, VIDEO_ID_F),
        ]
    )


def _focus_service() -> RetrievalService:
    return RetrievalService(store=_focus_stub_store(), embedder=EMBEDDER)


def test_focus_guarantees_top_rank():
    service = _focus_service()
    question, scope = "what are partial derivatives", _FOCUS_ORDER

    baseline = asyncio.run(service.retrieve(question, "course1", scope, T))
    # Relevance alone puts the PDF front matter far first -- the reported bug.
    assert [p.chunk_id for p in baseline] == ["d_pdf", "c_vid"]

    focused = asyncio.run(
        service.retrieve(question, "course1", scope, T, focus_resource_id=RES_VIDEO_F)
    )
    # The learner's video now leads despite a 6x lower raw score, and the PDF
    # is still available as context (nothing is dropped).
    assert [p.chunk_id for p in focused] == ["c_vid", "d_pdf"]
    assert len(focused) == 2

    scores = {p.chunk_id: p.score for p in focused}
    assert scores["c_vid"] == pytest.approx(_FOCUS_SCORE_VIDEO + FOCUS_SCORE_FLOOR)
    # Non-focused score is untouched: the floor is additive, and unfocused
    # scores stay on the retriever's own scale.
    assert scores["d_pdf"] == pytest.approx(_FOCUS_SCORE_PDF)

    # The floor must clear the retriever's whole score range, not just this one
    # gap: the fused score is bounded above by a perfect cosine match (1.0).
    assert FOCUS_SCORE_FLOOR > 1.0

    # Callers thread ChatRequest.video_id through, which is the video's own id
    # rather than the resource id chunks are indexed under. Both must match.
    by_video_id = asyncio.run(
        service.retrieve(question, "course1", scope, T, focus_resource_id=VIDEO_ID_F)
    )
    assert [p.chunk_id for p in by_video_id] == ["c_vid", "d_pdf"]


def test_focus_preserves_relative_order():
    # Two passages per resource, deliberately interleaved relevance.
    store = _StubStore(
        [
            _pdf_scored("d_hi", 3.0, RES_PDF_F),
            _pdf_scored("d_lo", 2.5, RES_PDF_F),
            _video_scored("c_lo", 0.4, RES_VIDEO_F, VIDEO_ID_F),
            _video_scored("c_hi", 0.9, RES_VIDEO_F, VIDEO_ID_F),
        ]
    )
    service = RetrievalService(store=store, embedder=EMBEDDER)
    question, scope = "what are partial derivatives", _FOCUS_ORDER

    baseline = asyncio.run(service.retrieve(question, "course1", scope, T))
    assert [p.chunk_id for p in baseline] == ["d_hi", "d_lo", "c_hi", "c_lo"]

    focused = asyncio.run(
        service.retrieve(question, "course1", scope, T, focus_resource_id=RES_VIDEO_F)
    )
    ids = [p.chunk_id for p in focused]
    # Focused group first, still ordered by its own scores (c_hi 0.9 > c_lo 0.4).
    assert ids[:2] == ["c_hi", "c_lo"]
    # Unfocused group intact and still ordered by its own scores.
    assert ids[2:] == ["d_hi", "d_lo"]
    # Ordering within each group is exactly the pre-focus ordering.
    assert ids[:2] == [p.chunk_id for p in baseline if p.chunk_id.startswith("c_")]
    assert ids[2:] == [p.chunk_id for p in baseline if p.chunk_id.startswith("d_")]

    scores = {p.chunk_id: p.score for p in focused}
    assert scores["c_hi"] == pytest.approx(0.9 + FOCUS_SCORE_FLOOR)
    assert scores["c_lo"] == pytest.approx(0.4 + FOCUS_SCORE_FLOOR)
    assert scores["d_hi"] == pytest.approx(3.0)
    assert scores["d_lo"] == pytest.approx(2.5)
    # Every focused passage beats every unfocused one, by construction.
    assert min(scores["c_hi"], scores["c_lo"]) > max(scores["d_hi"], scores["d_lo"])


def test_focus_none_preserves_baseline():
    service = _focus_service()
    question, scope = "what are partial derivatives", _FOCUS_ORDER

    implicit = asyncio.run(service.retrieve(question, "course1", scope, T))
    explicit_none = asyncio.run(
        service.retrieve(question, "course1", scope, T, focus_resource_id=None)
    )
    # Byte-for-byte identical to the pre-focus behaviour, scores included.
    assert implicit == explicit_none
    assert [p.chunk_id for p in implicit] == ["d_pdf", "c_vid"]
    assert {p.chunk_id: p.score for p in implicit} == {
        "d_pdf": pytest.approx(_FOCUS_SCORE_PDF),
        "c_vid": pytest.approx(_FOCUS_SCORE_VIDEO),
    }

    # An unknown focus id must also be a no-op, never a filter.
    unknown = asyncio.run(
        service.retrieve(
            question, "course1", scope, T, focus_resource_id="res_does_not_exist"
        )
    )
    assert [p.chunk_id for p in unknown] == ["d_pdf", "c_vid"]
    assert {p.chunk_id: p.score for p in unknown} == {
        "d_pdf": pytest.approx(_FOCUS_SCORE_PDF),
        "c_vid": pytest.approx(_FOCUS_SCORE_VIDEO),
    }


def test_strict_t_gate_unaffected_by_focus():
    # Real store path: the store gate and the post-retrieval assert both run
    # before the boost, so a boost can never resurrect a post-T chunk.
    focused_store = InMemoryVectorStore(
        [
            make_chunk(
                "c_early",
                RES_VIDEO_F,
                "video",
                "partial derivatives vary with respect to each input",
                5_000,
                12_000,
            ),
            make_chunk(
                "c_late",
                RES_VIDEO_F,
                "video",
                "partial derivatives treat other variables as constant",
                600_000,
                610_000,
            ),
            make_chunk(
                "c_overlap",
                RES_VIDEO_F,
                "video",
                "partial derivatives straddling the pause point",
                50_000,
                70_000,
            ),
            make_chunk(
                "d_pdf",
                RES_PDF_F,
                "pdf",
                "partial derivatives chapter notes",
                page_no=2,
            ),
        ]
    )
    service = RetrievalService(store=focused_store, embedder=EMBEDDER)
    passages = asyncio.run(
        service.retrieve(
            "what are partial derivatives",
            "course1",
            _FOCUS_ORDER,
            T,
            focus_resource_id=RES_VIDEO_F,
        )
    )
    ids = [p.chunk_id for p in passages]

    # Focus is on RES_VIDEO_F, yet no post-T / overlapping video chunk survives.
    assert "c_late" not in ids
    assert "c_overlap" not in ids
    assert set(ids) == {"c_early", "d_pdf"}
    # The surviving in-window video chunk carries its boosted score and keeps
    # strict-T provenance.
    by_id = {p.chunk_id: p for p in passages}
    assert by_id["c_early"].end_ms == 12_000
    assert by_id["c_early"].source_type == "video"

    # Same query with the video pushed entirely past T: the PDF survives, the
    # focused video resource contributes nothing rather than leaking.
    past_t = asyncio.run(
        service.retrieve(
            "what are partial derivatives",
            "course1",
            _FOCUS_ORDER,
            0,
            focus_resource_id=RES_VIDEO_F,
        )
    )
    assert [p.chunk_id for p in past_t] == ["d_pdf"]


def test_file_ingested_video_chunks_are_retrievable():
    # Regression: a video chunk ingested from a *local file* has
    # ``video_id = None`` (only YouTube ingestion sets it). Such chunks must
    # still be retrievable -- they are matched by ``resource_id``.
    #
    # The bug this pins: the focus floor was applied *after*
    # ``HybridRetriever.retrieve`` had already truncated to ``k``, so a focused
    # video resource whose chunks ranked below the cut on raw similarity was
    # discarded before the boost could re-rank it, and only the PDF resource
    # was ever returned. The wider pre-boost pool fixes it.
    video_resource = "res_file_video"
    pdf_resource = "res_file_pdf"

    # 12 in-scope PDF chunks, each lexically closer to the query, so every one
    # of them outranks the single video chunk on raw cosine similarity. With
    # k=8 the video chunk is only reachable if focus is ranked before the cut.
    pdf_chunks = [
        make_chunk(
            f"p_{i}",
            pdf_resource,
            "pdf",
            f"partial derivative chapter {i} worked example lecture notes",
            page_no=i + 1,
        )
        for i in range(12)
    ]
    video_chunk = replace(
        make_chunk(
            "v_0",
            video_resource,
            "video",
            "a partial derivative is the rate of change of a multivariable function",
            start_ms=0,
            end_ms=30_000,
        ),
        # File-ingested: no YouTube id on the chunk (only YouTube ingestion
        # sets one), so it can only be matched by resource_id.
        video_id=None,
    )
    assert video_chunk.video_id is None

    store = InMemoryVectorStore([*pdf_chunks, video_chunk])
    service = RetrievalService(store=store, embedder=EMBEDDER)

    passages = asyncio.run(
        service.retrieve(
            "what is a partial derivative",
            "course1",
            [video_resource, pdf_resource],
            60_000,
            k=8,
            focus_resource_id=video_resource,
        )
    )

    by_id = {p.chunk_id: p for p in passages}
    # The focused video chunk survives and takes top rank...
    assert passages[0].chunk_id == "v_0"
    assert passages[0].source_type == "video"
    assert by_id["v_0"].score >= FOCUS_SCORE_FLOOR
    # ...and the PDF resource is still present as unfocused context.
    assert any(p.source_type == "pdf" for p in passages)
    # Only in-scope resources were returned.
    assert {p.chunk_id for p in passages} <= {"v_0", *(f"p_{i}" for i in range(12))}

    # Without focus the pool is untouched by the fix: still top-k by score.
    unfocused = asyncio.run(
        service.retrieve(
            "what is a partial derivative",
            "course1",
            [video_resource, pdf_resource],
            60_000,
            k=8,
        )
    )
    assert len(unfocused) == 8
    assert all(p.score < FOCUS_SCORE_FLOOR for p in unfocused)

    # The strict gate is not weakened: past T the video chunk yields nothing
    # even though it is focused.
    past_t = asyncio.run(
        service.retrieve(
            "what is a partial derivative",
            "course1",
            [video_resource, pdf_resource],
            0,
            k=8,
            focus_resource_id=video_resource,
        )
    )
    assert "v_0" not in {p.chunk_id for p in past_t}
    assert all(p.source_type == "pdf" for p in past_t)

# ---------------------------------------------------------------------------
# (j) Cosine dimension guard: _cosine must never score mismatched vectors, and
#     the guard must not break correct vectors. The first regression was a
#     ``zip``-based ``_cosine`` that truncated a 1536-dim query against a
#     3072-dim chunk and returned a plausible but meaningless score, so ranking
#     was arbitrary. The second regression this guards against is an
#     over-eager guard that rejects valid input or breaks the known values.
# ---------------------------------------------------------------------------


def test_cosine_rejects_dimension_mismatch():
    from app.retrieval.store import EmbeddingDimensionMismatch, _cosine

    query = [1.0, 0.0, 0.0]
    chunk = [1.0, 0.0]  # shorter: zip() would silently return 1.0 here
    chunk_long = [1.0, 0.0, 0.0, 0.0]  # longer

    for mismatched in (chunk, chunk_long):
        with pytest.raises(EmbeddingDimensionMismatch):
            _cosine(query, mismatched)

    # The store converts the raise into a skip, so one wrong-width chunk cannot
    # abort the query -- and crucially is never given a truncated score.
    good = make_chunk("c_ok", "res_video", "video", "partial derivatives", 0, 1000)
    bad = make_chunk("c_bad", "res_video", "video", "partial derivatives", 0, 1000)
    bad.embedding = [1.0, 0.0]  # wrong width on purpose
    store = InMemoryVectorStore([good, bad])
    hits = asyncio.run(
        store.search("course1", ["res_video"], T, EMBEDDER.embed("partial"), k=8)
    )
    assert [h.chunk.id for h in hits] == ["c_ok"]


def test_cosine_matches_known_similarity():
    from app.retrieval.store import _cosine

    # Identical (already unit-length) vectors -> 1.0.
    assert _cosine([1.0, 0.0, 0.0], [1.0, 0.0, 0.0]) == pytest.approx(1.0)
    # Orthogonal -> 0.0.
    assert _cosine([1.0, 0.0, 0.0], [0.0, 1.0, 0.0]) == pytest.approx(0.0)
    # Opposite -> -1.0 (the full range must survive the guard).
    assert _cosine([1.0, 0.0, 0.0], [-1.0, 0.0, 0.0]) == pytest.approx(-1.0)
    # Scaling must not matter, and a real end-to-end query must score ~1.0
    # against a chunk built from the same text (guards the guard end-to-end).
    assert _cosine([2.0, 0.0], [5.0, 0.0]) == pytest.approx(1.0)
    chunk = make_chunk("c_1", "res_video", "video", "what is a partial derivative")
    assert _cosine(EMBEDDER.embed("what is a partial derivative"), chunk.embedding) == (
        pytest.approx(1.0)
    )
    # Zero vector stays 0.0 rather than dividing by zero.
    assert _cosine([0.0, 0.0], [1.0, 1.0]) == 0.0


# ---------------------------------------------------------------------------
# (g) Low-T video questions vs an unconstrained document in the same scope.
#
# Regression guards for the production starvation: at a small learner timestamp
# only a couple of video chunks pass the strict ``end_ms <= T`` gate, while a
# document's passages are never gated at all. The focused video's evidence must
# still reach the answer -- and the gate must stay exactly as strict.
# ---------------------------------------------------------------------------

LOW_T = 30_000
DOC_RID = "res_doc_lowt"
VID_RID = "res_video_lowt"
VID_EXT_ID = "ext_vid_lowt"
LOW_T_QUERY = "what equation does he write on screen"

#: 40 identical document passages. They are ungated by ``T`` by design and,
#: being near-duplicates of the question phrasing, they fill the whole candidate
#: pool -- the exact competition that used to squeeze the video evidence out.
DOC_BOILERPLATE = "equation written on screen notes page"


def _low_t_chunk(cid, rid, source_type, text, start_ms=None, end_ms=None, page=None):
    chunk = make_chunk(cid, rid, source_type, text, start_ms=start_ms, end_ms=end_ms, page_no=page)
    if source_type in ("video", "video_transcript"):
        chunk.video_id = VID_EXT_ID
    return chunk


def _low_t_store() -> InMemoryVectorStore:
    chunks = [
        _low_t_chunk(f"doc{i}", DOC_RID, "pdf", DOC_BOILERPLATE, page=1)
        for i in range(40)
    ]
    # The only two video chunks that satisfy ``end_ms <= 30000``.
    chunks.append(_low_t_chunk("vid_early", VID_RID, "video", "Partial derivatives", 0, 1_500))
    chunks.append(
        _low_t_chunk("vid_onscreen", VID_RID, "video", "f(x, y) = x^2 y + sin(y)", 2_000, 14_360)
    )
    return InMemoryVectorStore(chunks)


def test_low_t_video_question_is_not_starved_by_pdf():
    """A 30s-paused learner's on-screen question must return the video chunks.

    Both focus-id forms are covered: the resource id, and the *external* video
    id the chat API actually receives as ``ChatRequest.video_id`` (a different
    value from the resource id the chunks are indexed under). Both must resolve,
    or the rescue query is skipped and the video evidence is lost again.
    """
    scope = [DOC_RID, VID_RID]
    for focus in (VID_RID, VID_EXT_ID):
        service = RetrievalService(store=_low_t_store(), embedder=EMBEDDER)
        passages = asyncio.run(
            service.retrieve(
                LOW_T_QUERY, "course1", scope, LOW_T, k=8, focus_resource_id=focus
            )
        )
        ids = [p.chunk_id for p in passages]
        assert "vid_onscreen" in ids, (
            f"focus={focus!r}: the only in-gate on-screen evidence was crowded out "
            f"by ungated document chunks; got {ids}"
        )
        assert "vid_early" in ids
        # The focused group is floored above the document group, so it leads.
        assert ids[:2] == ["vid_early", "vid_onscreen"]
        # Every returned passage is still inside the strict gate / scope.
        assert all(p.end_ms is None or p.end_ms <= LOW_T for p in passages if p.source_type == "video")


def test_video_chunks_still_excluded_past_t():
    """Guard: no video chunk ending after T is admitted, focus or no focus."""
    chunks = [
        _low_t_chunk("vid_onscreen", VID_RID, "video", "f(x, y) = x^2 y + sin(y)", 2_000, 14_360),
        # Ends well after the pause point: a spoiler for a 30s learner.
        _low_t_chunk("vid_spoiler", VID_RID, "video", "the answer revealed later", 60_000, 95_000),
        # Straddles T: dropped by the strict ``end_ms <= T`` rule.
        _low_t_chunk("vid_overlap", VID_RID, "video", "straddles the pause", 25_000, 45_000),
        _low_t_chunk("doc0", DOC_RID, "pdf", DOC_BOILERPLATE, page=1),
    ]
    store = InMemoryVectorStore(chunks)
    scope = [DOC_RID, VID_RID]
    for focus in (VID_RID, VID_EXT_ID, None):
        service = RetrievalService(store=store, embedder=EMBEDDER)
        passages = asyncio.run(
            service.retrieve(
                LOW_T_QUERY, "course1", scope, LOW_T, k=8, focus_resource_id=focus
            )
        )
        ids = {p.chunk_id for p in passages}
        assert "vid_spoiler" not in ids, f"focus={focus!r}: post-T chunk leaked"
        assert "vid_overlap" not in ids, f"focus={focus!r}: overlapping chunk leaked"
        assert "vid_onscreen" in ids, f"focus={focus!r}: in-gate evidence missing"


def test_pdf_remains_ungated():
    """Guard: T never applies to documents, at any timestamp (T=0 included)."""
    store = _low_t_store()
    for t_ms in (0, LOW_T, 600_000):
        service = RetrievalService(store=store, embedder=EMBEDDER)
        passages = asyncio.run(
            service.retrieve(
                LOW_T_QUERY, "course1", [DOC_RID, VID_RID], t_ms, k=8, focus_resource_id=None
            )
        )
        assert passages, f"T={t_ms}: document scope returned nothing"
        assert all(p.source_type == "pdf" for p in passages), (
            f"T={t_ms}: an ungated document query must not be starved by video chunks "
            f"that happen to score highly; got {[p.source_type for p in passages]}"
        )
        assert all(p.end_ms is None for p in passages)


def test_no_regression_when_plenty_of_evidence():
    """A mid-video question with plenty of evidence still ranks sensibly."""
    store = InMemoryVectorStore(
        [
            _low_t_chunk("vid_onscreen", VID_RID, "video", "f(x, y) = x^2 y + sin(y)", 2_000, 14_360),
            _low_t_chunk("vid_mid_a", VID_RID, "video", "partial derivative df dx of the function", 40_000, 55_000),
            _low_t_chunk("vid_mid_b", VID_RID, "video", "partial derivative df dy with respect to y", 90_000, 105_000),
            _low_t_chunk("doc0", DOC_RID, "pdf", DOC_BOILERPLATE, page=1),
        ]
    )
    service = RetrievalService(store=store, embedder=EMBEDDER)
    passages = asyncio.run(
        service.retrieve(
            "what does he write for the partial derivative with respect to y",
            "course1",
            [DOC_RID, VID_RID],
            120_000,
            k=8,
            focus_resource_id=VID_RID,
        )
    )
    ids = [p.chunk_id for p in passages]
    assert "vid_mid_b" in ids, f"the directly relevant chunk must be present; got {ids}"
    # No focus: the on-screen chunk is the best lexical match and leads.
    unfocused = RetrievalService(store=store, embedder=EMBEDDER)
    baseline = asyncio.run(
        unfocused.retrieve(
            "what equation does he write on screen",
            "course1",
            [DOC_RID, VID_RID],
            120_000,
            k=8,
        )
    )
    assert baseline, "a mid-video on-screen question must return evidence"
    assert "vid_onscreen" in {p.chunk_id for p in baseline}
