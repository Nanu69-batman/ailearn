"""Helpers shared by the ingestion/retrieval tests (NOT a pytest conftest).

Plain factories and one fake, no fixtures. The pytest fixtures live in
``conftest.py`` because pytest only auto-loads a file named exactly
``conftest.py``, and this suite already had one.
"""

from __future__ import annotations

from app.ingestion.chunking import Chunk
from app.ingestion.embedder import HashingEmbedder


def make_embedder(dim: int = 256) -> HashingEmbedder:
    """Offline embedder for tests: deterministic, no model download."""
    return HashingEmbedder(dim=dim)


def make_chunk(
    chunk_id: str,
    *,
    resource_id: str = "res_video",
    course_id: str = "course_1",
    source_type: str = "video",
    text: str = "partial derivatives hold one variable constant",
    start_ms: int | None = 0,
    end_ms: int | None = 60_000,
    page_no: int | None = None,
    slide_no: int | None = None,
    video_id: str | None = "vid_1",
) -> Chunk:
    """Build a chunk for store tests.

    ``end_ms=None`` is a legal, gateable state -- not an oversight -- so tests
    can assert that a video chunk with no known end is excluded.
    """
    return Chunk(
        chunk_id=chunk_id,
        resource_id=resource_id,
        course_id=course_id,
        source_type=source_type,
        text=text,
        start_ms=start_ms,
        end_ms=end_ms,
        video_id=video_id,
        page_no=page_no,
        slide_no=slide_no,
        metadata={"kind": "transcript" if source_type == "video" else "document"},
    )


#: Real course media, verified 655.5s, used by the duration tests.
KNOWN_DURATION_MS = 655_500