"""Job registry + the ingest pipeline.

Response shapes are frozen by ``apps/web/lib/api.ts`` (read-only here --
``apps/web`` is never modified)::

    POST /courses/{courseId}/ingest         -> { job_id, resource_id, status }
    POST /courses/{courseId}/ingest-youtube -> { job_id, resource_id, status }
    GET  /ingestion/jobs/{jobId}            -> { job_id, resource_id,
                                                 status, error? }
    GET  /courses/{courseId}/resources      -> { resources: [...] }

``status`` is one of ``pending|processing|done|failed`` -- the web client
discriminates on exactly those four.

Ingest is a background task so the POST returns immediately with a job id; the
web client polls ``/ingestion/jobs/{id}``.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from app.ingestion import audio as audio_mod
from app.ingestion import visual as visual_mod
from app.ingestion import youtube as yt_mod
from app.ingestion.chunking import Chunk, TextSegment, chunk_segment
from app.ingestion.documents import extract
from app.ingestion.duration import media_duration_ms

log = logging.getLogger(__name__)

VIDEO_SUFFIXES = {".mp4", ".m4v", ".mov", ".webm", ".mkv", ".avi"}
DOC_SUFFIXES = {".pdf", ".pptx"}

#: Uploads are capped so a single request cannot wedge the API.
MAX_UPLOAD_BYTES = 512 * 1024 * 1024


@dataclass
class IngestJob:
    job_id: str
    resource_id: str
    status: str = "pending"
    error: str | None = None
    course_id: str | None = None
    detail: dict = field(default_factory=dict)


_JOBS: dict[str, IngestJob] = {}
_LOCK = threading.Lock()


def new_job(course_id: str, resource_id: str) -> IngestJob:
    job = IngestJob(job_id=f"job_{uuid.uuid4().hex[:12]}", resource_id=resource_id,
                    course_id=course_id)
    with _LOCK:
        _JOBS[job.job_id] = job
    return job


def get_job(job_id: str) -> IngestJob | None:
    with _LOCK:
        return _JOBS.get(job_id)


def update_job(job_id: str, **fields) -> None:
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is not None:
            for key, value in fields.items():
                setattr(job, key, value)


def clear_jobs() -> None:
    with _LOCK:
        _JOBS.clear()


def source_type_for(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        return "pdf"
    if suffix == ".pptx":
        return "pptx"
    if suffix in VIDEO_SUFFIXES:
        return "video"
    raise ValueError(f"unsupported file type {suffix or filename!r}")


def _store():
    from app.retrieval.registry import get_shared_store

    return get_shared_store()


def build_chunks(
    *,
    resource_id: str,
    course_id: str,
    source_type: str,
    text_segments: list[TextSegment] | None = None,
    transcript: list[audio_mod.TranscriptSegment] | None = None,
    visual: list[visual_mod.VisualSegment] | None = None,
    video_id: str | None = None,
    title: str | None = None,
) -> list[Chunk]:
    """Assemble indexable chunks from every extractor.

    Audio and visual chunks are tagged ``video`` so they flow through the same
    strict-T gate the store applies to transcripts. That gate is
    time-only; nothing distinguishes "spoken" from "on screen" in the
    timestamp semantics, and merging them here means one gate, not two.

    ``title`` is the source file's original name and is stamped onto every
    chunk, so ``GET /courses/{id}/resources`` reports what the learner
    uploaded rather than an internal extractor label like "transcript".
    """
    chunks: list[Chunk] = []
    index = 0
    for seg in text_segments or []:
        made = chunk_segment(
            seg,
            resource_id=resource_id,
            course_id=course_id,
            source_type=source_type,
            start_index=index,
            title=title,
        )
        chunks.extend(made)
        index += len(made)

    for cue in transcript or []:
        made = chunk_segment(
            TextSegment(text=cue.text, start_ms=cue.start_ms, end_ms=cue.end_ms,
                        kind="transcript"),
            resource_id=resource_id,
            course_id=course_id,
            source_type="video",
            video_id=video_id,
            start_index=index,
            title=title,
        )
        chunks.extend(made)
        index += len(made)

    for frame in visual or []:
        made = chunk_segment(
            TextSegment(text=frame.text, start_ms=frame.start_ms,
                        end_ms=frame.end_ms, kind="visual"),
            resource_id=resource_id,
            course_id=course_id,
            source_type="video",
            video_id=video_id,
            start_index=index,
            title=title,
        )
        chunks.extend(made)
        index += len(made)
    return chunks


def _clamp_to_duration(chunks: list[Chunk], duration_ms: int) -> list[Chunk]:
    """No chunk may claim content past the authoritative duration.

    Belt-and-braces with the frame/transcript arithmetic: a model-sourced
    boundary that slipped through still cannot push ``end_ms`` beyond the file.
    """
    out: list[Chunk] = []
    for chunk in chunks:
        if chunk.end_ms is not None and chunk.end_ms > duration_ms:
            chunk.end_ms = duration_ms
        if (
            chunk.start_ms is not None
            and chunk.end_ms is not None
            and chunk.end_ms <= chunk.start_ms
        ):
            log.warning("dropping degenerate chunk %s (end<=start)", chunk.chunk_id)
            continue
        out.append(chunk)
    return out


def ingest_media(
    path: str | Path,
    *,
    course_id: str,
    resource_id: str,
    video_id: str | None = None,
    with_visual: bool = True,
    max_visual_frames: int | None = 40,
) -> dict:
    """Audio + visual ingest for a video. Raises when duration is unknown.

    Refusing to index without a duration is deliberate: strict-T compares
    ``end_ms <= T`` and an unknown timeline makes that comparison meaningless.
    """
    path = Path(path)
    duration_ms = media_duration_ms(path)
    if duration_ms is None:
        raise ValueError(
            f"cannot determine duration of {path.name}; refusing to ingest "
            "time-gated content without an authoritative timeline"
        )

    transcript, audio_signals = audio_mod.transcribe(path, duration_ms=duration_ms)
    visual: list[visual_mod.VisualSegment] = []
    if with_visual:
        try:
            visual = visual_mod.extract_visual_segments(
                str(path), duration_ms=duration_ms, max_frames=max_visual_frames
            )
        except Exception as exc:  # noqa: BLE001 - visual is additive to audio
            log.warning("visual extraction failed for %s: %s", path.name, exc)

    chunks = build_chunks(
        resource_id=resource_id,
        course_id=course_id,
        source_type="video",
        transcript=transcript,
        visual=visual,
        video_id=video_id,
        # The real uploaded/downloaded filename, so the catalogue shows the
        # learner their own file name and not the extractor label.
        title=path.name,
    )
    chunks = _clamp_to_duration(chunks, duration_ms)
    written = _store().add(chunks)
    report = {
        "audio_chunks": sum(1 for c in chunks if c.metadata.get("kind") == "transcript"),
        "visual_chunks": sum(1 for c in chunks if c.metadata.get("kind") == "visual"),
        "total_chunks": written,
        "duration_ms": duration_ms,
        "max_end_ms": max((c.end_ms or 0 for c in chunks), default=0),
        "audio_signals": audio_signals,
    }
    log.info(
        "ingested %s: %d audio / %d visual chunk(s), max end_ms=%s of %s",
        path.name, report["audio_chunks"], report["visual_chunks"],
        report["max_end_ms"], duration_ms,
    )
    return report


def ingest_document(
    path: str | Path, *, course_id: str, resource_id: str, source_type: str
) -> dict:
    """Document ingest. Never time-gated, so no duration lookup happens."""
    path = Path(path)
    segments = extract(path, source_type)
    chunks = build_chunks(
        resource_id=resource_id,
        course_id=course_id,
        source_type=source_type,
        text_segments=segments,
        title=path.name,
    )
    written = _store().add(chunks)
    return {"document_chunks": written, "total_chunks": written}


def ingest_youtube(
    url: str, *, course_id: str, resource_id: str, with_visual: bool = True
) -> dict:
    """Captions first; download+ASR only when captions are unavailable."""
    from app.config import settings

    video_id = yt_mod.parse_youtube_id(url)
    if not video_id:
        raise ValueError(f"could not extract a YouTube video id from {url!r}")

    captions = yt_mod.fetch_captions(video_id)
    if captions:
        chunks = build_chunks(
            resource_id=resource_id,
            course_id=course_id,
            source_type="video",
            text_segments=captions,
            video_id=video_id,
        )
        # Caption ends come from the uploader's own timeline; without a
        # downloaded file there is no container to check them against, so they
        # are trusted as observed and only sorted, never invented.
        chunks.sort(key=lambda c: (c.start_ms or 0))
        written = _store().add(chunks)
        return {
            "video_chunks": written,
            "caption_chunks": written,
            "source": "captions",
            "video_id": video_id,
        }

    media = yt_mod.download_media(
        url, data_dir=settings.yt_data_dir, max_minutes=settings.yt_max_minutes
    )
    if media is None:
        raise ValueError(
            f"no captions and media download failed for {video_id}"
        )
    report = ingest_media(
        media, course_id=course_id, resource_id=resource_id,
        video_id=video_id, with_visual=with_visual,
    )
    report["source"] = "asr"
    report["video_id"] = video_id
    return report


def run_ingest(job: IngestJob, work) -> None:
    """Execute ``work()`` on a worker thread, recording the outcome on the job."""
    update_job(job.job_id, status="processing", error=None)
    try:
        report = work()
    except Exception as exc:
        log.exception("ingest job %s failed", job.job_id)
        update_job(
            job.job_id,
            status="failed",
            error=f"{type(exc).__name__}: {exc}",
        )
        return
    update_job(job.job_id, status="done", detail=report)


def schedule(job: IngestJob, work) -> None:
    """Run an ingest in the background so POST returns a job id immediately."""

    async def _run() -> None:
        await asyncio.to_thread(run_ingest, job, work)

    try:
        asyncio.get_running_loop().create_task(_run())
    except RuntimeError:
        # No loop (sync context): run inline. Slower, but correct.
        run_ingest(job, work)


def job_payload(job: IngestJob) -> dict:
    """Shape matching ``IngestJob`` in apps/web/lib/api.ts (exact keys)."""
    payload = {
        "job_id": job.job_id,
        "resource_id": job.resource_id,
        "status": job.status,
    }
    if job.error:
        payload["error"] = job.error
    return payload