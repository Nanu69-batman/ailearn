"""Document extraction for pdf/pptx.

Documents are NOT time-gated (``app/retrieval``), so segments here carry
``page_no``/``slide_no`` and ``start_ms``/``end_ms`` of ``None`` -- there is no
moment-in-time bound to enforce. Keeping the fields ``None`` rather than 0 is
what makes "docs are ungated" a structural fact rather than a special case in
the filter.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.ingestion.chunking import TextSegment

log = logging.getLogger(__name__)


def extract_pdf(path: str | Path) -> list[TextSegment]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    out: list[TextSegment] = []
    for index, page in enumerate(reader.pages, start=1):
        try:
            text = (page.extract_text() or "").strip()
        except Exception as exc:  # noqa: BLE001 - one bad page must not fail all
            log.warning("pdf page %d of %s unreadable: %s", index, path, exc)
            continue
        if text:
            out.append(TextSegment(text=text, page_no=index, kind="document"))
    log.info("extracted %d page segment(s) from %s", len(out), path)
    return out


def extract_pptx(path: str | Path) -> list[TextSegment]:
    from pptx import Presentation

    deck = Presentation(str(path))
    out: list[TextSegment] = []
    for index, slide in enumerate(deck.slides, start=1):
        parts: list[str] = []
        for shape in slide.shapes:
            if getattr(shape, "has_text_frame", False):
                text = (shape.text_frame.text or "").strip()
                if text:
                    parts.append(text)
            if getattr(shape, "has_table", False):
                for row in shape.table.rows:
                    cells = [c.text.strip() for c in row.cells if c.text.strip()]
                    if cells:
                        parts.append(" | ".join(cells))
        if parts:
            out.append(
                TextSegment(text="\n".join(parts), slide_no=index, kind="document")
            )
    log.info("extracted %d slide segment(s) from %s", len(out), path)
    return out


def extract(path: str | Path, source_type: str) -> list[TextSegment]:
    """Dispatch on the declared source type. Raises on an unknown type."""
    path = Path(path)
    if source_type == "pdf":
        return extract_pdf(path)
    if source_type == "pptx":
        return extract_pptx(path)
    raise ValueError(f"extract() handles documents only, got {source_type!r}")