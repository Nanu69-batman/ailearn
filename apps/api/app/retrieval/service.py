"""RetrievalService -- the awaitable seam ``ChatService._retrieval()`` expects.

Signature is fixed by ``app/chat/service.py``::

    await retrieve(query, course_id, resource_scope, current_time_ms,
                   k=8, focus_resource_id=None) -> list[RetrievedPassage]

Every returned passage carries the fields ``app.chat`` reads: ``chunk_id``,
``resource_id``, ``source_type``, ``text``, ``start_ms``, ``end_ms``,
``video_id``, ``page_no``, ``slide_no``, ``score``.

Scope and strict-T are enforced in the store's query (see
``app/retrieval/store.py``); this layer adds an assertion, not a filter.
"""

from __future__ import annotations

import asyncio
import logging

from app.retrieval.store import (
    TIME_GATED_SOURCE_TYPES,
    RetrievedPassage,
    assert_no_leakage,
)

log = logging.getLogger(__name__)

#: Re-exported so callers need not know the store's module layout.
__all__ = [
    "TIME_GATED_SOURCE_TYPES",
    "RetrievalService",
    "RetrievedPassage",
    "assert_no_leakage",
]


class RetrievalService:
    """Thin async wrapper over a vector store.

    Async because ``ChatService.chat`` awaits it and Chroma/HNSW work is
    blocking; the query runs in a worker thread so the event loop stays free.
    """

    def __init__(self, *, store, embedder=None) -> None:
        self._store = store
        self._embedder = embedder

    async def retrieve(
        self,
        query: str,
        course_id: str,
        resource_scope: list[str],
        current_time_ms: int,
        k: int = 8,
        focus_resource_id: str | None = None,
        prefer_recent: bool = False,
    ) -> list[RetrievedPassage]:
        passages = await asyncio.to_thread(
            self._store.query,
            query,
            course_id=course_id,
            resource_scope=resource_scope,
            current_time_ms=current_time_ms,
            k=k,
            focus_resource_id=focus_resource_id,
            prefer_recent=prefer_recent,
        )
        # Loud on a leak, silent on success. Never repairs. This runs even when
        # `prefer_recent` narrowed the pool, so the recency path gets the same
        # independent strict-T assertion as every other path.
        assert_no_leakage(
            passages,
            resource_scope=resource_scope,
            current_time_ms=current_time_ms,
        )
        return passages