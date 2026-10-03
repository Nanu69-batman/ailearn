"""Top-level retrieval service: embed -> scoped search -> gate -> fuse.

Single entry point used by the chat API (Phase 4). All imports of engines,
providers, and settings are lazy so this module (and ``app.main``) loads
without a database, an embedding key, or optional ML dependencies.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


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
            from app.ingestion.embedder import HashingEmbedder

            embedder = HashingEmbedder()
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
    ) -> list[RetrievedPassage]:
        """Embed the question, run hybrid retrieval, return top-k passages."""
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
        hits = await retriever.retrieve(
            question, course_id, allowed_resource_ids, current_time_ms, k=k
        )
        return [to_passage(h) for h in hits]


async def retrieve(
    question: str,
    course_id: str,
    allowed_resource_ids: Sequence[str],
    current_time_ms: int,
    k: int = 8,
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
        question, course_id, allowed_resource_ids, current_time_ms, k=k
    )
