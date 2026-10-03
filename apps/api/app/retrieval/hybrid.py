"""Hybrid retrieval: FTS keyword scores + vector scores fused via RRF.

Filtering always happens *before* fusion/reranking: vector hits arrive
pre-filtered from the store and are re-asserted here, and any FTS hits are
asserted too (an FTS provider cannot be trusted to enforce the boundary).
Nothing that fails :func:`app.retrieval.filters.assert_no_leakage` can reach
the fused ranking.

Timestamp gate (strict): video/``video_transcript`` chunks are visible only
when their entire span ends at or before the learner timestamp
(``end_ms <= T``); overlapping chunks (``start_ms <= T < end_ms``) and
NULL-``end_ms`` video chunks are dropped. PDF/PPT/notes/code are unaffected
by ``T``.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Protocol

from app.retrieval.filters import assert_no_leakage
from app.retrieval.store import ScoredChunk

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.ingestion.embedder import EmbeddingProvider
    from app.retrieval.store import VectorStore

log = logging.getLogger(__name__)


def reciprocal_rank_fusion(
    ranked_id_lists: Sequence[Sequence[str]], rrf_k: int = 60
) -> dict[str, float]:
    """Fuse rank lists: ``score(d) = sum(1 / (rrf_k + rank))`` (1-based ranks)."""
    fused: dict[str, float] = {}
    for ids in ranked_id_lists:
        for rank, cid in enumerate(ids, start=1):
            fused[cid] = fused.get(cid, 0.0) + 1.0 / (rrf_k + rank)
    return fused


class KeywordIndex(Protocol):
    """Full-text (BM25/FTS) provider. Must NOT be relied on for scoping."""

    def search_text(
        self,
        question: str,
        course_id: str,
        allowed_resource_ids: Sequence[str],
        current_time_ms: int,
        k: int,
    ) -> Sequence:
        """Return candidate chunks (``ScoredChunk`` or raw ``Chunk``)."""
        ...  # pragma: no cover - protocol


class Reranker(Protocol):
    """Cross-encoder hook: reorder only, never introduce new candidates."""

    def rerank(
        self, question: str, candidates: Sequence[ScoredChunk]
    ) -> list[ScoredChunk]:
        ...  # pragma: no cover - protocol


class NoOpReranker:
    """Default reranker: preserves order (real cross-encoder wired in later)."""

    def rerank(
        self, question: str, candidates: Sequence[ScoredChunk]
    ) -> list[ScoredChunk]:
        return list(candidates)


class HybridRetriever:
    """Combines filtered vector + keyword candidates via RRF, then reranks."""

    def __init__(
        self,
        store: VectorStore,
        embedder: EmbeddingProvider,
        fts: KeywordIndex | None = None,
        reranker: Reranker | None = None,
        rrf_k: int = 60,
    ) -> None:
        self._store = store
        self._embedder = embedder
        self._fts = fts
        self._reranker = reranker or NoOpReranker()
        self._rrf_k = rrf_k

    @staticmethod
    def _normalize(items: Sequence) -> list[ScoredChunk]:
        normalized: list[ScoredChunk] = []
        for item in items:
            if hasattr(item, "chunk"):
                normalized.append(item)
            else:  # raw Chunk from an FTS provider: rank matters, score unknown
                normalized.append(ScoredChunk(chunk=item, score=0.0))
        return normalized

    async def retrieve(
        self,
        question: str,
        course_id: str,
        allowed_resource_ids: Sequence[str],
        current_time_ms: int,
        k: int = 8,
        fetch_k: int = 20,
    ) -> list[ScoredChunk]:
        """Return up to ``k`` fused, reranked, fully scope-checked hits.

        The timestamp gate is strict: video chunks require ``end_ms <= T``
        (no content taught after the learner's pause point).
        """
        if k <= 0 or not allowed_resource_ids:
            return []
        query_vector = self._embedder.embed(question)
        vector_hits = await self._store.search(
            course_id, allowed_resource_ids, current_time_ms, query_vector, fetch_k
        )
        vector_hits, _ = assert_no_leakage(
            self._normalize(vector_hits), current_time_ms, allowed_resource_ids
        )

        fts_hits: list[ScoredChunk] = []
        if self._fts is not None:
            raw = self._fts.search_text(
                question, course_id, allowed_resource_ids, current_time_ms, fetch_k
            )
            if inspect.isawaitable(raw):
                raw = await raw
            fts_hits, _ = assert_no_leakage(
                self._normalize(raw or []), current_time_ms, allowed_resource_ids
            )

        if not fts_hits:
            ranked = sorted(vector_hits, key=lambda s: s.score, reverse=True)
        else:
            pool = {h.chunk.id: h for h in vector_hits}
            for h in fts_hits:
                pool.setdefault(h.chunk.id, h)
            fused = reciprocal_rank_fusion(
                [
                    [h.chunk.id for h in vector_hits],
                    [h.chunk.id for h in fts_hits],
                ],
                rrf_k=self._rrf_k,
            )
            ranked = sorted(
                pool.values(),
                key=lambda h: (fused.get(h.chunk.id, 0.0), h.score),
                reverse=True,
            )

        reranked = self._reranker.rerank(question, ranked)
        # Rerankers only reorder, but re-assert so a future implementation can
        # never smuggle an unscored/out-of-scope candidate into the answer.
        reranked, _ = assert_no_leakage(
            self._normalize(reranked), current_time_ms, allowed_resource_ids
        )
        return list(reranked[:k])
