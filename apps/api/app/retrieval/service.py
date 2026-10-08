"""Top-level retrieval service: embed -> scoped search -> gate -> fuse.

Single entry point used by the chat API (Phase 4). All imports of engines,
providers, and settings are lazy so this module (and ``app.main``) loads
without a database, an embedding key, or optional ML dependencies.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

#: Additive score floor granted to every passage of the focused resource.
#:
#: Why a floor rather than a multiplier: a multiplier can only ever shift the
#: margin. PDF front matter repeats the question phrase in its title, its
#: contents listing and every section heading, so its fused score can exceed the
#: transcript's by far more than any small factor (the shipped 1.15x was
#: measurably ineffective in production). A floor is order-independent: it makes
#: "focused" dominate "not focused" outright.
#:
#: Score range of the fused retriever (see app/retrieval/hybrid.py), which is
#: what the floor must clear:
#:
#:   * No FTS provider -- hits are the store hits verbatim, sorted by score.
#:     Scores are cosine similarity: ``[-1.0, 1.0]`` for
#:     :class:`~app.retrieval.store.InMemoryVectorStore` (``_cosine``) and for
#:     :class:`~app.retrieval.store.PostgresVectorStore`
#:     (``1 - (embedding <=> query)`` is pgvector cosine).
#:   * With an FTS provider -- :func:`~app.retrieval.hybrid.reciprocal_rank_fusion`
#:     yields ``sum(1 / (rrf_k + rank))`` in ``(0, 2/rrf_k]`` (0.0328 at
#:     ``rrf_k=60``), but that value is used *only* as a sort key. The returned
#:     ``ScoredChunk.score`` is still the pool member's raw store score (raw FTS
#:     chunks arrive with ``score=0.0`` via ``_normalize``).
#:
#: So the maximum achievable unfocused score is 1.0 (a perfect cosine match).
#: 1000.0 clears that by three orders of magnitude, which keeps the guarantee
#: intact even if a future reranker emits unbounded logits instead of cosine
#: scores, while remaining trivially finite. Never a filter -- every unfocused
#: passage stays in the result set. See :func:`_apply_focus_rank`.
FOCUS_SCORE_FLOOR = 1000.0

#: Candidate pool retrieved *before* :func:`_apply_focus_rank` runs, when a
#: focus is set.
#:
#: Why focus needs more than ``k`` candidates: the store truncates its own
#: ``fetch_k`` results before returning, and the focused resource's chunks
#: routinely rank outside that cut on raw similarity (a PDF's front matter
#: matches the question phrase far better than a spoken transcript does).
#: Ranking the focus floor *after* the cut means the focused chunks are already
#: gone and the floor can never rescue them -- the boost re-ranks only what it
#: receives, so a focused video resource was silently dropped in favour of
#: unrelated PDF chunks.
#:
#: Widening the pool alone is not sufficient (a low-scoring focused resource can
#: still fall outside any fixed pool), so the focused resource is additionally
#: retrieved as its own scoped query and unioned into the pool. See
#: :meth:`RetrievalService.retrieve`.
FOCUS_POOL_K = 20


@dataclass
class RetrievedPassage:
    """A retrieval hit with provenance metadata for citation validation."""

    chunk_id: str
    source_type: str
    text: str
    score: float
    video_id: str | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    page_no: int | None = None
    slide_no: int | None = None
    #: Owning resource. ``video_id`` is absent on some locally-ingested videos,
    #: so citation dedup must not use it as the sole resource identity.
    resource_id: str | None = None


def _apply_focus_rank(hits, focus_resource_id: str | None):
    """Re-rank fused hits: the focused resource's group is floored above the rest.

    Rule: ``focused -> FOCUS_SCORE_FLOOR + own_score``, everything else keeps
    its own score. Because :data:`FOCUS_SCORE_FLOOR` exceeds the maximum
    achievable unfocused score, every focused passage outranks every unfocused
    one regardless of relevance. Adding a constant leaves intra-group ordering
    by ``own_score`` intact, and the sort is stable, so the relative order of
    both groups is preserved exactly.

    Ordering-only by construction: no candidate is added, removed, or re-gated,
    so the scope check and the strict ``end_ms <= T`` timestamp gate that
    already ran inside :meth:`HybridRetriever.retrieve` are untouched.
    Unfocused passages stay in the result set (cross-resource context must
    remain available to the LLM).

    ``focus_resource_id`` is matched against either ``chunk.resource_id`` or
    ``chunk.video_id``: callers thread ``ChatRequest.video_id`` (the video's
    external id) through here, which is a different value from the resource id
    the chunks are indexed under.
    """
    from app.retrieval.store import ScoredChunk

    if not focus_resource_id or not hits:
        return hits

    def _is_focused(hit) -> bool:
        chunk = hit.chunk
        return (
            getattr(chunk, "resource_id", None) == focus_resource_id
            or getattr(chunk, "video_id", None) == focus_resource_id
        )

    rescored = [
        ScoredChunk(
            chunk=h.chunk,
            score=h.score + FOCUS_SCORE_FLOOR if _is_focused(h) else h.score,
        )
        for h in hits
    ]
    return sorted(rescored, key=lambda h: h.score, reverse=True)


def _resolve_focus_resource_ids(store, focus_resource_id: str, allowed_resource_ids) -> list[str]:
    """Map a focus id to the resource ids it owns, intersected with granted scope.

    ``focus_resource_id`` reaches this layer as ``ChatRequest.video_id``, which is
    the *external* video id for a YouTube-ingested resource and therefore a
    DIFFERENT value from the ``resource_id`` its chunks are indexed under (a
    locally-ingested file video has no external id at all, so its ``video_id``
    is ``None``). Both forms name the focused resource, so both must resolve --
    :func:`_apply_focus_rank` already matches either, and this keeps the rescue
    query in :meth:`RetrievalService.retrieve` consistent with it.

    Without the ``video_id`` branch the rescue query below was skipped entirely
    for a YouTube-style focus id (it is not a member of the granted scope, which
    lists resource ids), so a focused resource could still be starved out of the
    pool by an unconstrained document -- reproducing the original low-``T``
    starvation under the other id form.

    Safety: the result is always intersected with ``allowed_resource_ids``, so
    an out-of-scope focus id yields no query at all and scope is never widened.
    The store gate and ``assert_no_leakage`` re-run inside the rescue query.
    """
    allowed = set(allowed_resource_ids)
    resolved = {focus_resource_id} & allowed
    snapshot = getattr(store, "snapshot_chunks", None)
    if snapshot is None:
        # A store that cannot be enumerated (e.g. Postgres) can only be scoped
        # by the id itself; the intersection above is the whole safe answer.
        return sorted(resolved)
    for chunk in snapshot():
        if (
            getattr(chunk, "resource_id", None) in allowed
            and getattr(chunk, "video_id", None) == focus_resource_id
        ):
            resolved.add(chunk.resource_id)
    return sorted(resolved)


def to_passage(hit) -> RetrievedPassage:
    chunk = hit.chunk if hasattr(hit, "chunk") else hit
    return RetrievedPassage(
        chunk_id=chunk.id,
        source_type=chunk.source_type,
        text=chunk.text,
        score=float(getattr(hit, "score", 0.0)),
        video_id=chunk.video_id,
        start_ms=chunk.start_ms,
        end_ms=chunk.end_ms,
        page_no=chunk.page_no,
        slide_no=chunk.slide_no,
        resource_id=getattr(chunk, "resource_id", None),
    )


class RetrievalService:
    """Owns embedder + store + optional FTS/reranker; exposes ``retrieve``."""

    def __init__(
        self,
        store=None,
        embedder=None,
        fts=None,
        reranker=None,
        rrf_k: int = 60,
    ) -> None:
        if store is None:
            from app.retrieval.store import InMemoryVectorStore

            store = InMemoryVectorStore([])
        if embedder is None:
            # MUST be the same provider ingestion used, not a hardcoded hasher:
            # ``get_default_embedder()`` is the single source of truth for the
            # provider choice, shared with ``app.ingestion.jobs`` and the
            # ingestion router. Hardcoding ``HashingEmbedder`` here meant a
            # Gemini-ingested store (3072) was queried with 1536-dim vectors,
            # making every cosine score truncated and ranking arbitrary.
            from app.ingestion.embedder import get_default_embedder

            embedder = get_default_embedder()
        self._store = store
        self._embedder = embedder
        self._fts = fts
        self._reranker = reranker
        self._rrf_k = rrf_k

    async def retrieve(
        self,
        question: str,
        course_id: str,
        allowed_resource_ids: Sequence[str],
        current_time_ms: int,
        k: int = 8,
        focus_resource_id: str | None = None,
    ) -> list[RetrievedPassage]:
        """Embed the question, run hybrid retrieval, return top-k passages.

        When ``focus_resource_id`` is set, focused passages are given
        ``FOCUS_SCORE_FLOOR + own_score`` so the learner's active resource
        outranks everything else, and the hits are re-ranked. This happens
        strictly *after* retrieval, so it cannot widen scope or bypass the
        ``end_ms <= T`` gate.

        The focus rank is applied to a wider candidate pool
        (:data:`FOCUS_POOL_K`) than ``k`` and the result is truncated to ``k``
        afterwards, so a focused resource is not discarded by the top-``k``
        cut before its boost can be applied.
        """
        from app.retrieval.hybrid import HybridRetriever

        if not question or not question.strip():
            return []
        if not allowed_resource_ids or k <= 0:
            return []
        retriever = HybridRetriever(
            store=self._store,
            embedder=self._embedder,
            fts=self._fts,
            reranker=self._reranker,
            rrf_k=self._rrf_k,
        )
        # Over-fetch when focusing so the focus floor is applied *before* the
        # top-k cut; scoping and the strict end_ms <= T gate already ran inside
        # the retriever and are unaffected by how many candidates we keep.
        pool_k = max(k, FOCUS_POOL_K) if focus_resource_id else k
        hits = await retriever.retrieve(
            question, course_id, allowed_resource_ids, current_time_ms, k=pool_k
        )

        if focus_resource_id:
            # The store truncates its own ``fetch_k`` window before returning, so
            # a low-scoring focused resource can be absent from ``hits``
            # entirely even though it is in scope. Retrieve the focused resource
            # *alone* -- giving it a dedicated window it cannot be out-ranked
            # within -- and union the result in.
            #
            # ``_resolve_focus_resource_ids`` handles the external-``video_id``
            # form of the focus id as well as the resource-id form; before it
            # existed, a YouTube-style focus id was not a member of the granted
            # scope, so this rescue query never ran and the focused video stayed
            # starved by the ungated document chunks.
            #
            # Safety: this never widens scope. The focused ids are intersected
            # with the granted scope, so an out-of-scope focus id yields no query
            # at all; and the store gate plus ``assert_no_leakage`` both re-run
            # on this second query, so a focused video chunk ending after T stays
            # invisible exactly as it would have been.
            allowed_set = set(allowed_resource_ids)
            focus_resource_ids = _resolve_focus_resource_ids(
                self._store, focus_resource_id, allowed_set
            )
            if focus_resource_ids:
                focused_hits = await retriever.retrieve(
                    question,
                    course_id,
                    focus_resource_ids,
                    current_time_ms,
                    k=pool_k,
                )
                seen = {h.chunk.id for h in hits}
                hits = [*hits, *(h for h in focused_hits if h.chunk.id not in seen)]

        hits = _apply_focus_rank(hits, focus_resource_id)
        return [to_passage(h) for h in hits[:k]]


async def retrieve(
    question: str,
    course_id: str,
    allowed_resource_ids: Sequence[str],
    current_time_ms: int,
    k: int = 8,
    focus_resource_id: str | None = None,
    *,
    store=None,
    embedder=None,
    fts=None,
    reranker=None,
) -> list[RetrievedPassage]:
    """Convenience wrapper using default (or injected) components."""
    service = RetrievalService(
        store=store, embedder=embedder, fts=fts, reranker=reranker
    )
    return await service.retrieve(
        question,
        course_id,
        allowed_resource_ids,
        current_time_ms,
        k=k,
        focus_resource_id=focus_resource_id,
    )
