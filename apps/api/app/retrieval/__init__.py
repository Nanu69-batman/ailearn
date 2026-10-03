"""Public surface of the retrieval package (import-safe without a DB)."""

from app.retrieval.filters import (
    LeakageViolation,
    assert_no_leakage,
    build_scope_filter,
)
from app.retrieval.hybrid import (
    HybridRetriever,
    NoOpReranker,
    reciprocal_rank_fusion,
)
from app.retrieval.service import (
    RetrievalService,
    RetrievedPassage,
    retrieve,
    to_passage,
)
from app.retrieval.store import (
    VIDEO_SOURCE_TYPES,
    InMemoryVectorStore,
    PostgresVectorStore,
    ScoredChunk,
)

__all__ = [
    "VIDEO_SOURCE_TYPES",
    "HybridRetriever",
    "InMemoryVectorStore",
    "LeakageViolation",
    "NoOpReranker",
    "PostgresVectorStore",
    "RetrievalService",
    "RetrievedPassage",
    "ScoredChunk",
    "assert_no_leakage",
    "build_scope_filter",
    "reciprocal_rank_fusion",
    "retrieve",
    "to_passage",
]
