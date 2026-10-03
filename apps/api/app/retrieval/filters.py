"""Second-gate leakage assertions (run *after* store-level filtering).

Even though :class:`VectorStore` backends already enforce course/scope/T in
their own query path, this module re-checks every candidate before it may
enter fusion, reranking, or prompt assembly. Deterministic code — never the
LLM — owns the timestamp boundary.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from app.retrieval.store import VIDEO_SOURCE_TYPES

log = logging.getLogger(__name__)


def build_scope_filter(
    course_id: str,
    allowed_resource_ids: Sequence[str],
    current_time_ms: int,
) -> dict:
    """Pure helper describing the retrieval scope for a request.

    No I/O, no branching on content — just a canonical, auditable dict that
    callers can log/attach to evaluations.
    """
    t_ms = int(current_time_ms)
    return {
        "course_id": course_id,
        "resource_ids": sorted(set(allowed_resource_ids)),
        "current_time_ms": t_ms,
        "video_cutoff_ms": t_ms,
        "video_source_types": sorted(VIDEO_SOURCE_TYPES),
        "permitted_only": True,
    }


@dataclass
class LeakageViolation:
    """One candidate dropped by the second gate."""

    chunk_id: str
    reason: str  # "timestamp" | "scope"
    detail: str = ""


def _as_chunk(item):
    return item.chunk if hasattr(item, "chunk") else item


def assert_no_leakage(
    chunks: Sequence,
    current_time_ms: int,
    allowed_resource_ids: Sequence[str],
) -> tuple[list, list[LeakageViolation]]:
    """Drop out-of-scope or post-T transcript chunks.

    Strict gate: ``video``/``video_transcript`` chunks are kept only when
    their entire span ends at or before ``T`` (``end_ms <= T``), so a chunk
    overlapping the pause point (``start_ms <= T < end_ms``) is dropped and a
    chunk with ``end_ms == T`` is kept. A video chunk with NULL ``end_ms`` is
    excluded defensively (it cannot prove it ends before ``T``). PDF/PPT/
    note/code chunks are unaffected by ``T``. Returns ``(kept, violations)``
    preserving input order and input item type; violations are logged.
    """
    allowed = set(allowed_resource_ids or [])
    t_ms = int(current_time_ms)
    kept: list = []
    violations: list[LeakageViolation] = []
    for item in chunks:
        chunk = _as_chunk(item)
        chunk_id = str(getattr(chunk, "id", "?"))
        if chunk.resource_id not in allowed:
            violations.append(
                LeakageViolation(
                    chunk_id=chunk_id,
                    reason="scope",
                    detail=f"resource_id={chunk.resource_id!r} outside learner scope",
                )
            )
            log.warning(
                "retrieval leakage blocked: chunk %s outside learner scope "
                "(resource_id=%r)",
                chunk_id,
                chunk.resource_id,
            )
            continue
        if chunk.source_type in VIDEO_SOURCE_TYPES:
            # Strict gate: entire chunk must end at or before T.
            # NULL end_ms is excluded defensively (cannot prove end_ms <= T).
            if chunk.end_ms is None:
                violations.append(
                    LeakageViolation(
                        chunk_id=chunk_id,
                        reason="timestamp",
                        detail=f"end_ms=NULL (unbounded) vs T={t_ms}",
                    )
                )
                log.warning(
                    "retrieval leakage blocked: transcript chunk %s has NULL "
                    "end_ms but learner is at T=%dms",
                    chunk_id,
                    t_ms,
                )
                continue
            if chunk.end_ms > t_ms:
                violations.append(
                    LeakageViolation(
                        chunk_id=chunk_id,
                        reason="timestamp",
                        detail=f"end_ms={chunk.end_ms} > T={t_ms}",
                    )
                )
                log.warning(
                    "retrieval leakage blocked: transcript chunk %s ends at %dms "
                    "but learner is at T=%dms",
                    chunk_id,
                    chunk.end_ms,
                    t_ms,
                )
                continue
        kept.append(item)
    return kept, violations
