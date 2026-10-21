"""Retrieval: scope enforcement + the strict-T spoiler gate.

    store.py    ChromaVectorStore. Both obligations (granted resource_scope,
                strict-T on end_ms) are expressed in the Chroma ``where``
                filter, not as Python post-filtering.
    service.py  RetrievalService.retrieve() -- the awaitable seam
                ChatService._retrieval() binds to.
    registry.py Process-wide shared Chroma client.

--------------------------------------------------------------------------
SAFETY INVARIANT -- STRICT-T TIMESTAMP GATE
--------------------------------------------------------------------------
A video/visual passage is usable only when::

    end_ms IS NOT NULL AND end_ms <= T

Two rules the implementation must not lose:

1. The comparison is on ``end_ms``, never ``start_ms``. A gate relaxed to
   ``start_ms`` was measured at a **48% spoiler rate**
   (``submission/eval-results/baseline.json``).
2. ``end_ms IS NULL`` is **EXCLUDE**, deliberately -- not an UNKNOWN
   three-valued-logic accident. ADR-002 §5.1 explicitly forbids a future
   "simplification" that drops the null check. Chroma cannot express
   ``IS NOT NULL`` (metadata values are non-null), so unknown ends are stored
   as the sentinel ``NO_END_MS`` and the filter requires a real integer.

Docs (pdf/pptx) are NOT time-gated: a page is not a moment in time.

Layers, each of which has failed independently at some point in this
project's history -- the redundancy is deliberate:

1. the store's ``where`` filter (structural, see :func:`build_where`)
2. ``store.assert_no_leakage()``, raising rather than silently filtering
3. ``app.chat.service.apply_strict_timestamp()``, post-retrieval, re-checking
"""

from __future__ import annotations

from app.retrieval.store import (
    NO_END_MS,
    TIME_GATED_SOURCE_TYPES,
    ChromaVectorStore,
    RetrievedPassage,
    assert_no_leakage,
    build_where,
    chroma_metadata,
)

__all__ = [
    "NO_END_MS",
    "TIME_GATED_SOURCE_TYPES",
    "ChromaVectorStore",
    "RetrievedPassage",
    "assert_no_leakage",
    "build_where",
    "chroma_metadata",
]