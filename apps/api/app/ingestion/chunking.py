"""Chunking over LangChain splitters, with timestamp-aware splitting.

The one rule that is not negotiable
-----------------------------------
A chunk's ``end_ms`` must never exceed the last moment its content was
actually observed. Splitting a transcript window therefore only ever *shrinks*
the window; the code never widens, pads, or merges a chunk's bounds to reach a
target size, because widening a bound past the observation point is a spoiler
bug -- the strict-T gate keeps chunks with ``end_ms <= T`` (``app/retrieval``
docstring, ADR-002 §5.1), so a widened tail is material the learner has not
reached yet, served as though they had.

Concretely: a transcript segment at ``[58s, 61s]`` split in two yields
``[58s, 59.5s]`` and ``[59.5s, 61s]``. A split that would run past the segment
is dropped rather than extended.

Splitters are the LangChain library ones (``RecursiveCharacterTextSplitter``);
no hand-rolled sliding windows.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field

from langchain_text_splitters import RecursiveCharacterTextSplitter

log = logging.getLogger(__name__)

#: Target chunk size for transcript/document text, in characters. Roughly a
#: paragraph-to-paragraph window -- long enough to embed meaningfully, short
#: enough that a citation points at something citable.
CHUNK_CHARS = 900

#: Overlap in characters between adjacent splits. Overlap is applied *within*
#: a source segment and never across a timestamp boundary.
CHUNK_OVERLAP_CHARS = 120


@dataclass
class TextSegment:
    """One time-bounded (or page-bounded) unit of observed content.

    ``end_ms`` is the moment the content stops being true on screen/in audio.
    For documents both bounds are ``None``: a PDF page is not a moment in time
    and is deliberately not time-gated (``app/retrieval``).
    """

    text: str
    start_ms: int | None = None
    end_ms: int | None = None
    page_no: int | None = None
    slide_no: int | None = None
    kind: str = "transcript"


@dataclass
class Chunk:
    """An indexable unit: content plus the metadata the store filters on."""

    chunk_id: str
    resource_id: str
    course_id: str
    source_type: str
    text: str
    start_ms: int | None = None
    end_ms: int | None = None
    video_id: str | None = None
    page_no: int | None = None
    slide_no: int | None = None
    metadata: dict = field(default_factory=dict)


def chunk_id_for(resource_id: str, index: int, text: str) -> str:
    """Stable, content-addressed id.

    Stability matters because a citation printed to a learner must keep
    resolving after a re-ingest, so the id derives from the resource, the
    ordinal position, and a digest of the content.
    """
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
    return f"{resource_id}_c{index}_{digest}"


def _splitter() -> RecursiveCharacterTextSplitter:
    return RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_CHARS,
        chunk_overlap=CHUNK_OVERLAP_CHARS,
        length_function=len,
        keep_separator=True,
    )


def split_segment(
    segment: TextSegment,
) -> list[TextSegment]:
    """Split one segment, never widening any timestamp bound.

    For time-bounded content the split points are mapped back onto the
    segment's real timeline by character proportion, then **clamped** to the
    segment's own bounds. Clamping is what makes widening impossible: even if
    the proportional arithmetic rounds past ``end_ms``, the result cannot.
    """
    text = (segment.text or "").strip()
    if not text:
        return []
    if (segment.start_ms is None) != (segment.end_ms is None):
        # Half-bounded time content is malformed, and the two ways of
        # "fixing" it are both spoiler risks: fabricating the missing bound
        # claims observation that never happened, and dropping the timestamps
        # would make a *video* chunk look like an ungated document and let it
        # bypass strict-T entirely. So it is dropped and logged loudly.
        log.error(
            "dropping segment with only one timestamp bound "
            "(start_ms=%s end_ms=%s): a time-gated chunk must carry both",
            segment.start_ms, segment.end_ms,
        )
        return []
    if segment.start_ms is None:
        # Documents: bounds are absent by design, so splitting cannot leak.
        return [
            TextSegment(
                text=piece,
                page_no=segment.page_no,
                slide_no=segment.slide_no,
                kind=segment.kind,
            )
            for piece in _splitter().split_text(text)
            if piece.strip()
        ]

    pieces = [p for p in _splitter().split_text(text) if p.strip()]
    if not pieces:
        return []
    span = max(segment.end_ms - segment.start_ms, 1)
    total = max(len(text), 1)
    out: list[TextSegment] = []
    cursor = 0
    for piece in pieces:
        idx = text.find(piece[:40], cursor)
        if idx < 0:
            idx = cursor
        cursor = idx + len(piece)
        start = segment.start_ms + (idx * span) // total
        end = segment.start_ms + ((idx + len(piece)) * span) // total
        # The hard clamp. Never emit a bound outside [start_ms, end_ms].
        start = max(segment.start_ms, min(start, segment.end_ms))
        end = max(segment.start_ms, min(end, segment.end_ms))
        if end <= start:
            # A zero-length window carries no information and, worse, cannot
            # satisfy `end_ms <= T` at a boundary. Give it 1ms of extent --
            # but stay INSIDE the segment: bumping `end` to `start + 1` can
            # overshoot `segment.end_ms` by 1ms, which is still a widening.
            end = min(start + 1, segment.end_ms)
            if end <= start:
                # Zero-length source window: there is no extent to give the
                # piece without widening past what was observed, so the piece
                # is dropped. Losing a degenerate chunk is the cheap error;
                # inventing 1ms of observed time is not.
                log.warning(
                    "dropping piece of a zero-length segment [%s, %s]: no "
                    "extent can be given without widening the window",
                    segment.start_ms, segment.end_ms,
                )
                continue
        out.append(
            TextSegment(
                text=piece, start_ms=start, end_ms=end, kind=segment.kind
            )
        )
    return out


def chunk_segment(
    segment: TextSegment,
    *,
    resource_id: str,
    course_id: str,
    source_type: str,
    video_id: str | None = None,
    start_index: int = 0,
    title: str | None = None,
) -> list[Chunk]:
    """Turn one segment into indexable chunks with monotonic ids.

    ``title`` is the originating file's name, carried on every chunk so the
    resource catalogue can show the learner what they actually uploaded. It is
    optional and never invented: a caller with no filename passes ``None`` and
    the UI falls back to the resource id.
    """
    chunks: list[Chunk] = []
    for offset, piece in enumerate(split_segment(segment)):
        idx = start_index + offset
        chunks.append(
            Chunk(
                chunk_id=chunk_id_for(resource_id, idx, piece.text),
                resource_id=resource_id,
                course_id=course_id,
                source_type=source_type,
                text=piece.text,
                start_ms=piece.start_ms,
                end_ms=piece.end_ms,
                video_id=video_id,
                page_no=piece.page_no,
                slide_no=piece.slide_no,
                metadata={
                    "kind": piece.kind,
                    "ordinal": idx,
                    # "" not None: Chroma metadata values must be scalar, and
                    # an empty string is falsy so `resources()` reads it as
                    # "no filename known" rather than inventing one.
                    "title": (title or "").strip(),
                },
            )
        )
    return chunks