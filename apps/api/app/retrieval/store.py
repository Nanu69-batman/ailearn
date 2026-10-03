"""Vector retrieval backends behind a single protocol.

All backends must enforce the same deterministic scoping rules *inside* the
retrieval layer (never via LLM prompting):

  * ``course_id`` equality,
  * ``resource_id`` membership in the learner's allowed set,
  * ``permitted = true``,
  * timestamp guard: transcript chunks with ``end_ms > T`` are invisible
    (the whole chunk must end at or before the learner's pause point;
    non-transcript sources are unaffected by ``T``).

``PostgresVectorStore`` enforces these in SQL; ``InMemoryVectorStore`` enforces
the identical predicate in Python (tests/demo without a database). The
post-retrieval assertion in :mod:`app.retrieval.filters` is the second gate.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:  # pragma: no cover - typing only, no runtime cost
    from app.ingestion.models import Chunk

log = logging.getLogger(__name__)

# Transcript-bearing source types gated by the learner timestamp T.
# Ingestion writes ``source_type="video"`` (resource type) while the plan/ADR
# call the transcript source ``"video_transcript"``; both are gated.
VIDEO_SOURCE_TYPES = frozenset({"video", "video_transcript"})

# SQL fragment enforcing the strict timestamp boundary: a video chunk is
# visible only if its *entire* span ends at or before T
# (``end_ms <= :current_time_ms``), so no retrieved chunk contains content
# taught after the learner's pause point. It is a strict tightening of the
# legacy spec clause ``(source_type != 'video_transcript' OR start_ms <= :T)``:
# it additionally gates ``source_type = 'video'`` (what ingestion actually
# stores for transcript chunks, see app/ingestion/jobs.py) and compares
# ``end_ms`` instead of ``start_ms`` so chunks overlapping T
# (``start_ms <= T < end_ms``) are dropped.
# NULL ``end_ms`` choice: EXCLUDE. A video chunk with NULL end_ms cannot prove
# it ends before T, so it is treated as leakage. The explicit
# ``end_ms IS NOT NULL`` makes that intent testable (plain ``end_ms <= :T``
# would also filter NULLs via UNKNOWN, but implicitly).
TIMESTAMP_GUARD_CLAUSE = (
    "(source_type NOT IN ('video', 'video_transcript')"
    " OR (end_ms IS NOT NULL AND end_ms <= :current_time_ms))"
)


@dataclass
class ScoredChunk:
    """A retrieved chunk with its similarity score (higher = more similar)."""

    chunk: Chunk
    score: float


class VectorStore(Protocol):
    """Repository seam for vector search (pgvector now, swappable later)."""

    async def search(
        self,
        course_id: str,
        allowed_resource_ids: Sequence[str],
        current_time_ms: int,
        query_vector: Sequence[float],
        k: int = 8,
    ) -> list[ScoredChunk]:
        """Return up to ``k`` chunks, already scoped to course/scope/T."""
        ...  # pragma: no cover - protocol


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _in_scope(chunk: Chunk, course_id: str, allowed: frozenset, t_ms: int) -> bool:
    if chunk.course_id != course_id:
        return False
    if chunk.resource_id not in allowed:
        return False
    if not chunk.permitted:
        return False
    if chunk.source_type in VIDEO_SOURCE_TYPES:
        # Strict gate: the whole chunk must end at or before T.
        # NULL end_ms is excluded defensively (cannot prove end_ms <= T);
        # non-video sources are unaffected by T.
        if chunk.end_ms is None:
            log.warning(
                "retrieval scope blocked: video chunk %s has NULL end_ms "
                "(learner T=%dms)",
                getattr(chunk, "id", "?"),
                t_ms,
            )
            return False
        if chunk.end_ms > t_ms:
            return False
    return True


class InMemoryVectorStore:
    """Cosine-similarity store for tests/demo; enforces the same predicate."""

    def __init__(self, chunks: Sequence[Chunk] | None = None) -> None:
        self._chunks: list[Chunk] = list(chunks) if chunks else []

    def index(self, chunks: Sequence[Chunk]) -> None:
        """Add chunks to the store (replacement keyed by chunk id)."""
        known = {c.id for c in chunks}
        self._chunks = [c for c in self._chunks if c.id not in known]
        self._chunks.extend(chunks)

    async def search(
        self,
        course_id: str,
        allowed_resource_ids: Sequence[str],
        current_time_ms: int,
        query_vector: Sequence[float],
        k: int = 8,
    ) -> list[ScoredChunk]:
        if k <= 0 or not allowed_resource_ids:
            return []
        allowed = frozenset(allowed_resource_ids)
        scored: list[ScoredChunk] = []
        for chunk in self._chunks:
            if chunk.embedding is None:
                continue
            if not _in_scope(chunk, course_id, allowed, current_time_ms):
                continue
            scored.append(
                ScoredChunk(chunk=chunk, score=_cosine(query_vector, chunk.embedding))
            )
        scored.sort(key=lambda s: s.score, reverse=True)
        return scored[:k]


class PostgresVectorStore:
    """pgvector-backed store. SQL enforces course + scope + T + permitted."""

    def __init__(self, database_url: str | None = None, engine=None) -> None:
        # No connection is opened here; the engine is created lazily on first
        # search so importing this module never requires a live database.
        self._database_url = database_url
        self._engine = engine

    def _get_engine(self):  # lazy: sqlalchemy imported only when needed
        if self._engine is not None:
            return self._engine
        from sqlalchemy.ext.asyncio import create_async_engine

        url = self._database_url
        if url is None:  # fall back to app settings only at connect time
            from app.config import settings

            url = settings.database_url
        self._engine = create_async_engine(url)
        return self._engine

    async def close(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None

    def build_search_sql(
        self,
        course_id: str,
        allowed_resource_ids: Sequence[str],
        current_time_ms: int,
        k: int = 8,
    ) -> tuple[str, dict]:
        """Build the scoped SELECT (pure: no DB access). Returns (sql, params).

        ``:query_vector`` (pgvector ``"[v,...]"`` literal) and ``:k`` are left
        as bind parameters for the caller to supply.
        """
        ids = sorted(set(allowed_resource_ids))
        if ids:
            rid_placeholders = ", ".join(f":rid_{i}" for i in range(len(ids)))
            scope_clause = f"resource_id IN ({rid_placeholders})"
        else:
            scope_clause = "1 = 0"  # empty scope matches nothing, stays valid SQL
        sql = (
            "SELECT id, resource_id, course_id, source_type, video_id,"
            " start_ms, end_ms, page_no, slide_no, chunk_index,"
            " text, text_hash, embed_model, permitted,"
            " 1 - (embedding <=> CAST(:query_vector AS vector)) AS score"
            " FROM chunks"
            f" WHERE course_id = :course_id AND {scope_clause}"
            " AND permitted = true"
            f" AND {TIMESTAMP_GUARD_CLAUSE}"
            " ORDER BY embedding <=> CAST(:query_vector AS vector)"
            " LIMIT :k"
        )
        params: dict = {
            "course_id": course_id,
            "current_time_ms": int(current_time_ms),
            "k": int(k),
        }
        params.update({f"rid_{i}": rid for i, rid in enumerate(ids)})
        return sql, params

    @staticmethod
    def _row_to_scored(row) -> ScoredChunk:
        from app.ingestion.models import Chunk

        chunk = Chunk(
            id=row["id"],
            resource_id=row["resource_id"],
            course_id=row["course_id"],
            source_type=row["source_type"],
            chunk_index=row["chunk_index"],
            text=row["text"],
            text_hash=row["text_hash"],
            embed_model=row["embed_model"],
            video_id=row["video_id"],
            start_ms=row["start_ms"],
            end_ms=row["end_ms"],
            page_no=row["page_no"],
            slide_no=row["slide_no"],
            permitted=row["permitted"],
            embedding=None,
        )
        return ScoredChunk(chunk=chunk, score=float(row["score"]))

    async def search(
        self,
        course_id: str,
        allowed_resource_ids: Sequence[str],
        current_time_ms: int,
        query_vector: Sequence[float],
        k: int = 8,
    ) -> list[ScoredChunk]:
        if k <= 0 or not allowed_resource_ids:
            return []
        from sqlalchemy import text as sa_text

        sql, params = self.build_search_sql(
            course_id, allowed_resource_ids, current_time_ms, k
        )
        params["query_vector"] = "[" + ",".join(repr(float(v)) for v in query_vector) + "]"
        engine = self._get_engine()
        async with engine.connect() as conn:
            result = await conn.execute(sa_text(sql), params)
            rows = result.mappings().all()
        log.debug("pgvector search returned %d rows (k=%d)", len(rows), k)
        return [self._row_to_scored(r) for r in rows]
