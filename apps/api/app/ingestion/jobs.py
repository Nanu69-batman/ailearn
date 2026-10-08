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

# --- Visual fragment merging ---------------------------------------------------
#
# Gemini transcribes on-screen writing at the granularity it was *written*, so a
# single equation arrives as a burst of tiny fragments ("= f(x, y)",
# "= x^2 y", "= + sin(y)") seconds apart. Each fragment alone is a poor retrieval
# target: too short to embed meaningfully and too contextless for a learner. So
# adjacent fragments of the SAME KIND are merged back into one chunk.
#
# Two hard rules keep this from becoming a spoiler bug:
#
#  1. The merged chunk's ``end_ms`` is the MAXIMUM of the merged fragments' own
#     ``end_ms`` -- the last moment writing was actually observed on screen. It
#     is never computed from the merge window, the span cap, or the next
#     fragment's start. A gap between two fragments is a gap, not content, so a
#     learner paused inside it must still not receive the second fragment.
#  2. Merging is bounded by VISUAL_MERGE_MAX_GAP_MS and VISUAL_MERGE_MAX_SPAN_MS,
#     so unrelated fragments can never collapse into one far-future chunk whose
#     timestamp lies about what was on screen when.
VISUAL_MERGE_MAX_GAP_MS = 4_000
VISUAL_MERGE_MAX_SPAN_MS = 20_000

# --- Speech window tightening --------------------------------------------------
#
# ``chunk_transcript`` emits each window with ``start_ms``/``end_ms`` taken from
# the FIRST and LAST segment merged into it. Those two bounds are faithful and
# stay that way: ``end_ms`` is exactly the last segment's own end time, never the
# window edge, so this change cannot widen an end past real spoken content and
# cannot break the strict ``end_ms <= T`` spoiler gate. Visual and figure chunks
# are untouched for the same reason.
#
# What merging DOES cost is the start: a chunk inherits its first segment's start,
# so a sentence spoken in the middle of a 30-60s window is stamped up to a full
# window early (measured on the demo clip: a line Gemini places at 287.0s was
# indexed in a chunk starting at 258.9s, 28s early). Overlapping windows also put
# the same sentence in several chunks whose citations disagree.
#
# So speech windows are made smaller and are not allowed to merge across a large
# gap. Both changes SHRINK a chunk's interval towards its own content; neither
# ever extends it. The floor of 12s keeps a window long enough to embed as a
# coherent passage.
SPEECH_CHUNK_MIN_WINDOW_S = 12.0
SPEECH_CHUNK_MAX_WINDOW_S = 24.0
SPEECH_CHUNK_OVERLAP_S = 6.0
# A pause longer than this ends the window: merging across it would give a chunk
# a start_ms minutes before its content was actually spoken. Same rationale as
# VISUAL_MERGE_MAX_GAP_MS.
SPEECH_MERGE_MAX_GAP_MS = 4_000

# Characters that make an on-screen fragment "maths" rather than a prose label.
# Merging never crosses kinds: an equation being written must not absorb a
# heading that happens to be on screen nearby.
_MATH_CHARS = ("=", "^", "(", ")", "+", "-", "*", "/", "<", ">", "_")


def _visual_kind(text: str) -> str:
    return "math" if any(c in text for c in _MATH_CHARS) else "label"


def _merge_visual_fragments(
    parts: list[tuple[str, int, int]]
) -> list[tuple[str, int, int]]:
    """Group ``(text, start_ms, end_ms)`` fragments into mergeable runs.

    Returns groups of the same shape, each covering exactly the interval its
    own parts covered: ``start_ms`` is the min start and ``end_ms`` the max end
    of the parts actually merged. Both bounds therefore come from observed
    content only -- see the safety note above the constants.
    """
    ordered = sorted(parts, key=lambda p: (p[1], p[2]))
    groups: list[tuple[str, int, int]] = []
    run: list[tuple[str, int, int]] = []
    for item in ordered:
        if run:
            prev = run[-1]
            gap = item[1] - prev[2]
            span = item[2] - run[0][1]
            joinable = (
                _visual_kind(prev[0]) == _visual_kind(item[0])
                and 0 <= gap <= VISUAL_MERGE_MAX_GAP_MS
                and span <= VISUAL_MERGE_MAX_SPAN_MS
            )
            if not joinable:
                groups.append(_collapse(run))
                run = []
        run.append(item)
    if run:
        groups.append(_collapse(run))
    return groups


def _collapse(run: list[tuple[str, int, int]]) -> tuple[str, int, int]:
    return (
        "\n".join(text for text, _, _ in run).strip(),
        min(start for _, start, _ in run),
        max(end for _, _, end in run),
    )


def _visual_chunks(
    segments: list[Segment],
    resource_row: Mapping[str, Any],
    embed_model: str,
    seen_hashes: set[str],
) -> list[Chunk]:
    """One chunk per on-screen written-text group, with its own timestamps.

    Kept separate from ``chunk_transcript`` on purpose: that chunker merges
    segments into 30-60s windows, which would smear an equation written at 30s
    across a window ending at 60s and make its ``end_ms`` a lie. On-screen text
    must keep the exact interval during which it was visible, because that
    ``end_ms`` is what the strict ``end_ms <= T`` retrieval gate tests.

    Short same-kind fragments written seconds apart (one equation captured piece
    by piece) ARE merged, by ``_merge_visual_fragments``, so a learner asking
    about the whole expression gets one usable chunk instead of three
    contextless stubs. The merge never extends ``end_ms`` past the last observed
    writing, and every chunk still gets non-null ``start_ms``/``end_ms``. A
    visual segment lacking a real end time cannot prove it finished before T, so
    it is dropped rather than emitted with a null/zero end -- a null ``end_ms``
    is excluded by the defensive filter in ``retrieval/store.py``, which is the
    correct outcome.
    """
    from app.ingestion.transcribe import VisualSegment

    resource_id = str(resource_row["id"])
    course_id = str(resource_row["course_id"])
    video_id = resource_row.get("video_id")

    # Pass 1: keep only fragments that are usable and not already indexed as
    # speech. Dropping before the merge stops an already-indexed fragment from
    # being smuggled back in as part of a merged neighbour.
    parts: list[tuple[str, int, int]] = []
    for seg in segments:
        if not isinstance(seg, VisualSegment):
            continue
        text = seg.text.strip()
        if not text:
            continue
        if seg.start_ms is None or seg.end_ms is None or seg.end_ms <= seg.start_ms:
            log.warning(
                "dropping visual segment without a usable end_ms: %s", text[:60]
            )
            continue
        h = text_hash(text)
        if h in seen_hashes:
            # Already indexed as speech in this resource; a second copy would
            # double-weight it in the embedding space and skew top-k.
            log.info("dropping visual text already indexed as speech: %s", text[:60])
            continue
        parts.append((text, seg.start_ms, seg.end_ms))

    # Pass 2: one chunk per merged group, timestamped from the group's own parts.
    chunks: list[Chunk] = []
    for text, start_ms, end_ms in _merge_visual_fragments(parts):
        h = text_hash(text)
        if h in seen_hashes:
            continue
        seen_hashes.add(h)
        chunks.append(
            Chunk(
                id=hashlib.sha1(
                    (
                        resource_id
                        + "|visual|"
                        + str(start_ms)
                        + "-"
                        + str(end_ms)
                        + "|"
                        + h
                    ).encode("utf-8")
                ).hexdigest(),
                resource_id=resource_id,
                course_id=course_id,
                source_type="video",
                video_id=video_id,
                start_ms=start_ms,
                end_ms=end_ms,
                chunk_index=len(chunks),
                text=text,
                text_hash=h,
                embed_model=embed_model,
            )
        )
    return chunks


def _figure_chunks(
    segments: list[Segment],
    resource_row: Mapping[str, Any],
    embed_model: str,
    seen_hashes: set[str],
) -> list[Chunk]:
    """One chunk per figure/diagram element, with its own exact timestamps.

    ``FigureSegment`` subclasses ``VisualSegment``, but figure elements are NOT
    run through ``_merge_visual_fragments``: that merge exists because Gemini
    transcribes one equation in pieces ("= f(x, y)", "= x^2 y"), which are
    fragments of a single expression. Two adjacent figure entries are two
    distinct things on screen ("a yellow curve", then "he draws a blue arrow
    pointing from x to y"), so merging them would answer neither question well
    and would widen ``end_ms`` across a window the learner may not have reached.

    Every chunk therefore carries the element's own observed ``start_ms`` and
    ``end_ms``, both non-null: an element without a real end time cannot prove it
    finished before T, so it is dropped rather than emitted with a null/zero end
    (which the defensive filter in ``retrieval/store.py`` would exclude anyway).
    ``source_type`` stays ``"video"`` so the strict ``end_ms <= T`` gate applies
    to figures exactly as it does to speech.
    """
    from app.ingestion.transcribe import FigureSegment

    resource_id = str(resource_row["id"])
    course_id = str(resource_row["course_id"])
    video_id = resource_row.get("video_id")
    chunks: list[Chunk] = []
    for seg in segments:
        if not isinstance(seg, FigureSegment):
            continue
        text = seg.text.strip()
        if not text:
            continue
        if seg.start_ms is None or seg.end_ms is None or seg.end_ms <= seg.start_ms:
            log.warning("dropping figure segment without a usable end_ms: %s", text[:60])
            continue
        h = text_hash(text)
        if h in seen_hashes:
            # Already indexed as speech or as on-screen text for this resource;
            # a second copy would double-weight it and skew top-k.
            log.info("dropping figure text already indexed: %s", text[:60])
            continue
        seen_hashes.add(h)
        chunks.append(
            Chunk(
                id=hashlib.sha1(
                    (
                        resource_id
                        + "|figure|"
                        + str(seg.start_ms)
                        + "-"
                        + str(seg.end_ms)
                        + "|"
                        + h
                    ).encode("utf-8")
                ).hexdigest(),
                resource_id=resource_id,
                course_id=course_id,
                source_type="video",
                video_id=video_id,
                start_ms=seg.start_ms,
                end_ms=seg.end_ms,
                chunk_index=len(chunks),
                text=text,
                text_hash=h,
                embed_model=embed_model,
            )
        )
    return chunks


def _split_speech_on_gaps(
    segments: list[Segment], max_gap_ms: int = SPEECH_MERGE_MAX_GAP_MS
) -> list[list[Segment]]:
    """Split speech segments into runs that never span a large pause.

    A chunk built from segments either side of a long silence would carry a
    ``start_ms`` from before the pause, so a sentence spoken after it inherits a
    start far earlier than it was uttered. Splitting the run keeps every chunk
    inside one continuous stretch of speech. Purely a SPLIT: no segment is
    dropped, re-timed, or given a wider interval than it already had.
    """
    runs: list[list[Segment]] = []
    current: list[Segment] = []
    for seg in segments:
        if current:
            prev = current[-1]
            if (
                prev.end_ms is not None
                and seg.start_ms is not None
                and seg.start_ms - prev.end_ms > max_gap_ms
            ):
                runs.append(current)
                current = []
        current.append(seg)
    if current:
        runs.append(current)
    return runs


def _chunk_segments(
    segments: list[Segment],
    resource_row: Mapping[str, Any],
    embed_model: str,
) -> list[Chunk]:
    rtype = str(resource_row.get("type", "note"))
    if rtype == "video":
        from app.ingestion.transcribe import FigureSegment, VisualSegment

        # Figures are pulled out first: they subclass VisualSegment, so leaving
        # them in `visual` would send them through the on-screen fragment merge
        # and smear several distinct elements into one chunk.
        figures = [s for s in segments if isinstance(s, FigureSegment)]
        visual = [
            s for s in segments if isinstance(s, VisualSegment) and not isinstance(s, FigureSegment)
        ]
        speech = [
            s for s in segments if not isinstance(s, VisualSegment)
        ]
        # Each continuous run of speech is chunked separately so a window can
        # never bridge a long pause (see _split_speech_on_gaps), and every
        # window is capped so its start_ms sits close to its own content.
        chunks: list[Chunk] = []
        for run in _split_speech_on_gaps(speech):
            chunks.extend(
                chunk_transcript(
                    run,
                    resource_id=str(resource_row["id"]),
                    course_id=str(resource_row["course_id"]),
                    source_type=rtype,
                    video_id=resource_row.get("video_id"),
                    embed_model=embed_model,
                    min_window_s=SPEECH_CHUNK_MIN_WINDOW_S,
                    max_window_s=SPEECH_CHUNK_MAX_WINDOW_S,
                    overlap_s=SPEECH_CHUNK_OVERLAP_S,
                )
            )
        # `seen` spans all three arrays, so text already indexed as speech, as
        # on-screen text, or as an earlier figure is never indexed twice for one
        # resource.
        seen = {c.text_hash for c in chunks}
        chunks.extend(_visual_chunks(visual, resource_row, embed_model, seen))
        chunks.extend(_figure_chunks(figures, resource_row, embed_model, seen))
        for i, c in enumerate(chunks):
            c.chunk_index = i
        return chunks
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
