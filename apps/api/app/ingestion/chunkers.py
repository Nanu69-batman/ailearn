"""Transcript chunker: 30-60s speech-aligned windows with 10-15s overlap.

Prefers sentence boundaries (never splits mid-sentence when punctuation is
present), but force-closes at 1.5x the max window so unpunctuated ASR output
still yields bounded, timestamp-useful chunks instead of one giant span.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Iterable

from app.ingestion.models import Chunk, Segment, text_hash

log = logging.getLogger(__name__)

SENTENCE_END = re.compile(r'[.!?]["\')\]]?\s*$')

DEFAULT_MIN_WINDOW_S = 30.0
DEFAULT_MAX_WINDOW_S = 60.0
DEFAULT_OVERLAP_S = 12.5
# Hard cap: close the window here even mid-sentence (ASR text often has no
# punctuation). Without this, one chunk can span the whole video and the
# end_ms timestamp gate excludes it for every T before the video end.
FORCE_CLOSE_MULTIPLIER = 1.5


def _ends_sentence(text: str) -> bool:
    return bool(SENTENCE_END.search(text.strip()))


def _chunk_id(source_id: str, segment_key: str, text: str) -> str:
    return hashlib.sha1(
        (source_id + "|" + segment_key + "|" + text_hash(text)).encode("utf-8")
    ).hexdigest()


def chunk_transcript(
    segments: Iterable[Segment],
    resource_id: str,
    course_id: str,
    source_type: str = "video",
    video_id: str | None = None,
    embed_model: str = "",
    min_window_s: float = DEFAULT_MIN_WINDOW_S,
    max_window_s: float = DEFAULT_MAX_WINDOW_S,
    overlap_s: float = DEFAULT_OVERLAP_S,
) -> list[Chunk]:
    segs = [s for s in segments if s.text.strip()]
    if not segs:
        return []

    chunks: list[Chunk] = []

    def emit(window: list[Segment]) -> None:
        text = " ".join(s.text.strip() for s in window).strip()
        if not text:
            return
        first, last = window[0], window[-1]
        idx = len(chunks)
        chunks.append(
            Chunk(
                id=_chunk_id(resource_id, first.segment_key(), text),
                resource_id=resource_id,
                course_id=course_id,
                source_type=source_type,
                video_id=video_id or first.video_id,
                start_ms=first.start_ms,
                end_ms=last.end_ms,
                chunk_index=idx,
                text=text,
                text_hash=text_hash(text),
                embed_model=embed_model,
            )
        )

    window: list[Segment] = []
    force_close_s = max_window_s * FORCE_CLOSE_MULTIPLIER
    for seg in segs:
        if window and window[0].start_ms is not None and seg.end_ms is not None:
            span_s = (seg.end_ms - window[0].start_ms) / 1000.0
            if span_s > force_close_s:
                log.info(
                    "force-closing transcript window at %.1fs (no sentence end)",
                    span_s,
                )
                emit(window)
                overlap_start = (window[-1].end_ms or 0) - int(overlap_s * 1000)
                window = [
                    s
                    for s in window
                    if s.end_ms is not None and s.end_ms > overlap_start
                ]
        # Close before exceeding max window when we are at a sentence boundary.
        # Close before exceeding max window when we are at a sentence boundary.
        if (
            window
            and window[0].start_ms is not None
            and seg.end_ms is not None
            and (seg.end_ms - window[0].start_ms) / 1000.0 > max_window_s
            and _ends_sentence(window[-1].text)
        ):
            emit(window)
            overlap_start = (window[-1].end_ms or 0) - int(overlap_s * 1000)
            window = [
                s
                for s in window
                if s.end_ms is not None and s.end_ms > overlap_start
            ]

        window.append(seg)

        if (
            window[0].start_ms is not None
            and seg.end_ms is not None
            and (seg.end_ms - window[0].start_ms) / 1000.0 >= min_window_s
            and _ends_sentence(seg.text)
        ):
            emit(window)
            overlap_start = (window[-1].end_ms or 0) - int(overlap_s * 1000)
            window = [
                s
                for s in window
                if s.end_ms is not None and s.end_ms > overlap_start
            ]

    if window:
        text = " ".join(s.text.strip() for s in window).strip()
        # Skip a trailing window that is wholly redundant with the overlap of
        # the previous chunk.
        if text and not (chunks and text in chunks[-1].text):
            emit(window)

    for i, c in enumerate(chunks):
        c.chunk_index = i
    return chunks
