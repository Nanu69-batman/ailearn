"""Ingestion orchestrator: extract -> chunk -> embed -> upsert."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app.config import settings
from app.ingestion.chunkers import chunk_transcript
from app.ingestion.embedder import EmbeddingProvider, get_default_embedder
from app.ingestion.extractors import extract
from app.ingestion.models import Chunk, Segment, text_hash

log = logging.getLogger(__name__)


def _chunk_segments(
    segments: list[Segment],
    resource_row: Mapping[str, Any],
    embed_model: str,
) -> list[Chunk]:
    rtype = str(resource_row.get("type", "note"))
    if rtype == "video":
        return chunk_transcript(
            segments,
            resource_id=str(resource_row["id"]),
            course_id=str(resource_row["course_id"]),
            source_type=rtype,
            video_id=resource_row.get("video_id"),
            embed_model=embed_model,
        )
    # Non-transcript resources: one chunk per segment unless segments are tiny.
    chunks: list[Chunk] = []
    buf: list[Segment] = []
    buf_len = 0

    def flush() -> None:
        nonlocal buf, buf_len
        if not buf:
            return
        text = "\n\n".join(s.text.strip() for s in buf).strip()
        first, last = buf[0], buf[-1]
        chunks.append(
            Chunk(
                id=hashlib.sha1(
                    (
                        str(resource_row["id"])
                        + "|"
                        + first.segment_key()
                        + "|"
                        + text_hash(text)
                    ).encode("utf-8")
                ).hexdigest(),
                resource_id=str(resource_row["id"]),
                course_id=str(resource_row["course_id"]),
                source_type=rtype,
                video_id=resource_row.get("video_id"),
                start_ms=first.start_ms,
                end_ms=last.end_ms,
                page_no=first.page_no,  # first page when spanning pages (never null)
                slide_no=first.slide_no,  # first slide when spanning slides
                chunk_index=len(chunks),
                text=text,
                text_hash=text_hash(text),
                embed_model=embed_model,
            )
        )
        buf = []
        buf_len = 0

    for seg in segments:
        buf.append(seg)
        buf_len += len(seg.text)
        page_key = (seg.page_no, seg.slide_no)
        if buf_len >= 1200 or (
            buf and (buf[0].page_no, buf[0].slide_no) != page_key
        ):
            flush()
    flush()
    for i, c in enumerate(chunks):
        c.chunk_index = i
    return chunks


async def _db_reachable(url: str) -> bool:
    try:
        from sqlalchemy.ext.asyncio import create_async_engine

        engine = create_async_engine(url, pool_pre_ping=True)
        try:
            async with engine.connect() as conn:
                await conn.execute(__import__("sqlalchemy").text("SELECT 1"))
            return True
        finally:
            await engine.dispose()
    except Exception:  # noqa: BLE001 - any failure means "not reachable"
        return False


async def _upsert_chunks(url: str, chunks: list[Chunk], resource_id: str) -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM chunks WHERE resource_id = :rid"), {"rid": resource_id}
            )
            for c in chunks:
                row = c.to_row()
                row["embedding"] = (
                    "[" + ",".join(repr(v) for v in c.embedding) + "]"
                    if c.embedding is not None
                    else None
                )
                await conn.execute(
                    text(
                        "INSERT INTO chunks (id, resource_id, course_id, source_type,"
                        " video_id, start_ms, end_ms, page_no, slide_no, chunk_index,"
                        " text, text_hash, embed_model, embedding, permitted) VALUES"
                        " (:id, :resource_id, :course_id, :source_type, :video_id,"
                        " :start_ms, :end_ms, :page_no, :slide_no, :chunk_index,"
                        " :text, :text_hash, :embed_model, CAST(:embedding AS vector),"
                        " :permitted)"
                    ),
                    row,
                )
    finally:
        await engine.dispose()


async def _set_job_status(url: str, job_id: str | None, status: str, error: str | None = None) -> None:
    if not job_id:
        return
    try:
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine

        engine = create_async_engine(url)
        try:
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "UPDATE ingestion_jobs SET status = :status, error = :error,"
                        " updated_at = now() WHERE id = :id"
                    ),
                    {"status": status, "error": error, "id": job_id},
                )
        finally:
            await engine.dispose()
    except Exception as exc:  # noqa: BLE001 - logging only
        log.warning("ingestion_jobs update failed: %s", exc)


async def run_ingestion(
    resource_row: Mapping[str, Any],
    asset_path: str | Path,
    embedder: EmbeddingProvider | None = None,
    database_url: str | None = None,
    job_id: str | None = None,
) -> list[Chunk]:
    """Run extract->chunk->embed->upsert. Returns chunks (dry-run when no DB)."""
    embedder = embedder or get_default_embedder()
    url = database_url if database_url is not None else settings.database_url
    db_ok = bool(url) and await _db_reachable(url)

    if db_ok:
        await _set_job_status(url, job_id, "processing")

    try:
        segments = extract(asset_path, str(resource_row.get("type", "note")), resource_row.get("video_id"))
        chunks = _chunk_segments(segments, resource_row, embedder.name)
        texts = [c.text for c in chunks]
        vectors = embedder.embed_batch(texts) if texts else []
        for c, v in zip(chunks, vectors):
            c.embedding = v
        if db_ok:
            await _upsert_chunks(url, chunks, str(resource_row["id"]))
            await _set_job_status(url, job_id, "done")
        else:
            log.info("DB unreachable; dry-run ingestion returning %d chunks", len(chunks))
        return chunks
    except Exception as exc:
        if db_ok:
            await _set_job_status(url, job_id, "failed", str(exc))
        raise
