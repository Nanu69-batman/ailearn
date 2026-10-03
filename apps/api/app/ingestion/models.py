"""Canonical ingestion models mirroring the chunks table schema."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field


def text_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


@dataclass
class Segment:
    """A source-aligned unit of extracted text (one caption line, one PDF page, etc.)."""

    text: str
    source_type: str  # video | pdf | pptx | note | code
    video_id: str | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    page_no: int | None = None
    slide_no: int | None = None
    index: int = 0  # position within the resource

    def segment_key(self) -> str:
        if self.start_ms is not None:
            return f"t{self.start_ms}-{self.end_ms}"
        if self.page_no is not None:
            return f"p{self.page_no}"
        if self.slide_no is not None:
            return f"s{self.slide_no}"
        return f"i{self.index}"


@dataclass
class CanonicalDocument:
    resource_id: str
    course_id: str
    source_type: str
    asset_uri: str = ""
    video_id: str | None = None
    sha256: str = ""
    segments: list[Segment] = field(default_factory=list)


@dataclass
class Chunk:
    id: str
    resource_id: str
    course_id: str
    source_type: str
    chunk_index: int
    text: str
    text_hash: str
    embed_model: str
    video_id: str | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    page_no: int | None = None
    slide_no: int | None = None
    permitted: bool = True
    embedding: list[float] | None = None

    def to_row(self) -> dict:
        return {
            "id": self.id,
            "resource_id": self.resource_id,
            "course_id": self.course_id,
            "source_type": self.source_type,
            "video_id": self.video_id,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "page_no": self.page_no,
            "slide_no": self.slide_no,
            "chunk_index": self.chunk_index,
            "text": self.text,
            "text_hash": self.text_hash,
            "embed_model": self.embed_model,
            "embedding": self.embedding,
            "permitted": self.permitted,
        }
