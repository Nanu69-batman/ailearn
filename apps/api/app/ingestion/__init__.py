"""Ingestion: media -> timestamped, indexable chunks.

    duration.py  authoritative container duration (mvhd/mehd, PyAV fallback)
    audio.py     faster-whisper primary, Gemini secondary, timeline reconciled
    visual.py    PyAV frame sampling -> Gemini description, timestamps from
                 FRAME INDEX ARITHMETIC only
    chunking.py  LangChain splitters; a chunk's end_ms never exceeds the last
                 moment its content was observed
    embedder.py  offline hashing embedder by default, Gemini when configured
    documents.py pdf/pptx text extraction (never time-gated)
    jobs.py      job registry + the ingest pipeline
    router.py    POST /courses/{id}/ingest, /ingest-youtube, job + resource GETs

Safety: every time-gated chunk must carry a real ``end_ms``. A video whose
duration cannot be determined is refused, not indexed (see
``app.ingestion.duration``).
"""

from __future__ import annotations