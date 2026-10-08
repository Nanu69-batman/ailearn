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
import threading
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

# Indexed-chunk dimensions already reported as mismatched, so the warning in
# ``InMemoryVectorStore.search`` fires once per dimension rather than per chunk.
_warned_dims: set[int] = set()


class EmbeddingDimensionMismatch(ValueError):
    """A query vector and an indexed chunk vector have different widths.

    Cosine similarity is only defined on equal-length vectors. ``zip`` truncates
    to the shorter one, which produces a number that *looks* like a relevance
    score but has no meaning -- and therefore silently corrupts ranking. This is
    raised instead of truncating; see :func:`_cosine`.
    """


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
    """Cosine similarity, raising on any dimension mismatch.

    Chosen behaviour: RAISE (:class:`EmbeddingDimensionMismatch`) rather than
    skip-and-log. ``skip`` would still require the caller to decide what a
    missing candidate means for ranking, and a store where every chunk is
    skipped degenerates into "no results" -- which is indistinguishable, at the
    call site, from an empty scope. Raising makes the misconfiguration
    impossible to miss at the single point where the vectors meet, and
    ``InMemoryVectorStore.search`` converts it into an explicit skip + warning
    so one bad chunk cannot take down a whole query.

    The previous implementation zipped the vectors, so a 1536-dim query scored
    against the first 1536 dims of a 3072-dim Gemini chunk: a plausible float,
    silently meaningless, and therefore arbitrary ranking.
    """
    if len(a) != len(b):
        raise EmbeddingDimensionMismatch(
            f"cosine similarity requires equal dimensions, got {len(a)} and {len(b)}"
        )
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
        # Guards every read and write of ``_chunks``.
        #
        # Why: ingestion mutates this store from a background thread while
        # request threads concurrently index / search / serialise it. Without
        # a lock the read-modify-write in ``index`` is not atomic across the
        # ``_chunks = [...]`` rebind and the following ``.extend``, so two
        # concurrent ingests silently drop each other's chunks, and a snapshot
        # taken mid-mutation can serialise a half-built list. A plain
        # ``threading.Lock`` (not an RLock) is enough: no public method calls
        # another one while holding it.
        self._lock = threading.Lock()
        self._chunks: list[Chunk] = list(chunks) if chunks else []

    def snapshot_chunks(self) -> list[Chunk]:
        """Return a shallow copy of the indexed chunks under the lock.

        Callers that need to serialise or iterate the chunk list (snapshot
        persistence, long scans) take one of these instead of reaching into
        ``_chunks``, so they cannot observe a list being rebuilt underneath
        them and cannot hold the lock for the duration of their own work.
        """
        with self._lock:
            return list(self._chunks)

    def index(self, chunks: Sequence[Chunk]) -> None:
        """Add chunks to the store (replacement keyed by chunk id).

        Dedupes by chunk id *and* drops any stale chunk re-keyed under the
        same resource (re-ingestion of one resource must not accumulate
        orphaned chunks).
        """
        known = {c.id for c in chunks}
        resource_ids = {c.resource_id for c in chunks}
        # One critical section: read-modify-write must be atomic, otherwise a
        # concurrent index() rebinds ``_chunks`` between the rebuild and the
        # extend below and the extended chunks are written to an orphaned list.
        with self._lock:
            rebuilt = [
                c
                for c in self._chunks
                if c.id not in known and c.resource_id not in resource_ids
            ]
            rebuilt.extend(chunks)
            self._chunks = rebuilt

    def remove_resource(self, resource_id: str) -> int:
        """Drop every indexed chunk for a resource; returns removed count."""
        with self._lock:
            before = len(self._chunks)
            self._chunks = [
                c for c in self._chunks if c.resource_id != resource_id
            ]
            return before - len(self._chunks)

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
        # Scan a lock-protected copy rather than ``self._chunks`` directly:
        # the snapshot is consistent, and the (potentially long) scoring loop
        # runs without holding the lock, so a concurrent ingest is not
        # blocked by an in-flight query.
        for chunk in self.snapshot_chunks():
            if chunk.embedding is None:
                continue
            if not _in_scope(chunk, course_id, allowed, current_time_ms):
                continue
            # ``_cosine`` raises on a dimension mismatch (see its docstring).
            # Here the mismatch is caught and the chunk SKIPPED, so one
            # wrong-width chunk degrades ranking instead of aborting the whole
            # query, while a chunk can never contribute a truncated, meaningless
            # score. Warn once per width rather than once per chunk.
            try:
                score = _cosine(query_vector, chunk.embedding)
            except EmbeddingDimensionMismatch:
                if len(chunk.embedding) not in _warned_dims:
                    _warned_dims.add(len(chunk.embedding))
                    log.warning(
                        "vector store dimension mismatch: query is %d-dim but "
                        "chunk %s is %d-dim; that chunk is SKIPPED rather than "
                        "scored, so ranking is not silently corrupted. Re-ingest "
                        "with the active embedder (see app.ingestion.embedder."
                        "get_default_embedder) so both sides use the same dim.",
                        len(query_vector),
                        getattr(chunk, "id", "?"),
                        len(chunk.embedding),
                    )
                continue
            scored.append(ScoredChunk(chunk=chunk, score=score))
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
