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
import re
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


#: Titles are display labels, not storage keys: an absurdly long name is a
#: denial of the sources panel, never of information. Capped so a hostile
#: filename cannot bloat every chunk's metadata.
MAX_TITLE_LENGTH = 200

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def sanitize_filename(raw: str | None) -> str | None:
    """Reduce an attacker-controlled upload filename to a safe display title.

    A browser can send a full local path (``C:\\Users\\x\\lecture.mp4`` or a
    POSIX equivalent), so the directory component is stripped for both
    separators before anything else. Control characters are removed, the
    result is trimmed and capped at :data:`MAX_TITLE_LENGTH` with the
    extension preserved.

    Returns ``None`` when nothing usable survives. A title is never invented:
    an honest empty state lets the UI fall back to the resource id, whereas a
    fabricated one is indistinguishable from a real one.
    """
    if not raw:
        return None
    name = _CONTROL_CHARS.sub("", str(raw))
    # Strip both separators explicitly: the server's own separator is not
    # necessarily the one the client used.
    name = name.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not name or set(name) <= {"."}:
        return None
    if len(name) > MAX_TITLE_LENGTH:
        suffix = Path(name).suffix
        if 0 < len(suffix) <= 20:
            name = name[: MAX_TITLE_LENGTH - len(suffix)] + suffix
        else:
            name = name[:MAX_TITLE_LENGTH]
    return name or None


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


#: Distinguishes "caller did not say" (fall back to the file's own name, which
#: is correct for a direct on-disk ingest) from "caller said there is no usable
#: filename" (record no title at all -- never the staged name).
_UNSET = object()


def _resolve_title(path: Path, title: str | None | object) -> str | None:
    if title is _UNSET:
        return sanitize_filename(path.name)
    return sanitize_filename(title)  # type: ignore[arg-type]


def ingest_media(
    path: str | Path,
    *,
    course_id: str,
    resource_id: str,
    video_id: str | None = None,
    with_visual: bool = True,
    max_visual_frames: int | None = None,
    title: str | None | object = _UNSET,
) -> dict:
    """Audio + visual ingest for a video. Raises when duration is unknown.

    Refusing to index without a duration is deliberate: strict-T compares
    ``end_ms <= T`` and an unknown timeline makes that comparison meaningless.

    ``title`` is the name the learner uploaded. Upload callers pass the
    sanitised original filename explicitly, because ``path`` is the staged
    ``{resource_id}.{ext}`` asset and its name says nothing about the file the
    learner picked. Passing ``None`` records no title rather than falling back
    to the staged name.

    ``max_visual_frames`` used to default to ``40``. That is the shipped bug
    this function now documents: 40 frames at a 4s interval is the first 160
    seconds of a 655-second lecture, so the remaining eight minutes were indexed
    with nothing visual, and the job still reported ``status: done`` with
    "129 chunks". It defaults to no cap, and if a caller does pass one the
    report is forced to a non-``complete`` visual status so the truncation is
    stated rather than inferred.
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
    visual_report = visual_mod.VisualPassReport(duration_ms=duration_ms)
    warnings: list[str] = []
    if with_visual:
        try:
            visual_report = visual_mod.extract_visual_pass(
                str(path), duration_ms=duration_ms, max_frames=max_visual_frames
            )
        except Exception as exc:  # noqa: BLE001 - visual is additive to audio
            log.warning("visual extraction failed for %s: %s", path.name, exc)
            # Audio is genuinely useful and is already transcribed, so this is
            # a degraded pass rather than a failed job -- but it is now a
            # *recorded* degradation. The old code swallowed the exception and
            # reported the same success either way.
            warnings.append(
                f"visual extraction failed: {type(exc).__name__}: {exc}"
            )
            visual_report = visual_mod.VisualPassReport(
                expected_indices=visual_mod.iter_frame_indices(duration_ms),
                duration_ms=duration_ms,
                failures=[f"{type(exc).__name__}: {exc}"],
            )
        visual = visual_report.segments
        warnings.extend(visual_report.warnings)

    chunks = build_chunks(
        resource_id=resource_id,
        course_id=course_id,
        source_type="video",
        transcript=transcript,
        visual=visual,
        video_id=video_id,
        # The real uploaded/downloaded filename, so the catalogue shows the
        # learner their own file name and not the staged asset name or an
        # extractor label.
        title=_resolve_title(path, title),
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
        # Honest accounting for the visual pass. `visual_status` is the field
        # that must never be read as "the video was fully indexed": it is
        # "complete" only when every frame on the timeline was described.
        "visual_status": visual_report.status if with_visual else "skipped",
        "visual_expected_frames": len(visual_report.expected_indices),
        "visual_described_frames": len(visual_report.covered_indices),
        "visual_coverage_pct": visual_report.coverage_pct,
        "visual_span_pct": visual_report.span_pct,
        "warnings": warnings,
    }
    log.info(
        "ingested %s: %d audio / %d visual chunk(s), max end_ms=%s of %s",
        path.name, report["audio_chunks"], report["visual_chunks"],
        report["max_end_ms"], duration_ms,
    )
    if with_visual and visual_report.status != "complete":
        log.error(
            "ingest of %s is DEGRADED: visual status=%s, %d/%d frames described "
            "(%.1f%% of the timeline, span %.1f%% of the duration)",
            path.name, visual_report.status, report["visual_described_frames"],
            report["visual_expected_frames"], visual_report.coverage_pct,
            visual_report.span_pct,
        )
    return report


def ingest_document(
    path: str | Path,
    *,
    course_id: str,
    resource_id: str,
    source_type: str,
    title: str | None | object = _UNSET,
) -> dict:
    """Document ingest. Never time-gated, so no duration lookup happens.

    ``title`` follows the same rule as :func:`ingest_media`: it is the name the
    learner uploaded, passed explicitly by upload callers because ``path`` is
    the staged ``{resource_id}.{ext}`` asset.
    """
    path = Path(path)
    segments = extract(path, source_type)
    chunks = build_chunks(
        resource_id=resource_id,
        course_id=course_id,
        source_type=source_type,
        text_segments=segments,
        title=_resolve_title(path, title),
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
    """Shape matching ``IngestJob`` in apps/web/lib/api.ts (exact keys).

    The four keys the web client discriminates on are always present and never
    renamed. Anything extra is purely additive and omitted unless the ingest
    actually produced something to say -- a pending job is still exactly
    ``{job_id, resource_id, status}`` -- so the frozen contract cannot break
    and a degraded visual pass cannot be silent.
    """
    payload = {
        "job_id": job.job_id,
        "resource_id": job.resource_id,
        "status": job.status,
    }
    if job.error:
        payload["error"] = job.error
    detail = job.detail or {}
    for key in ("visual_status", "visual_coverage_pct", "visual_span_pct"):
        if key in detail:
            payload[key] = detail[key]
    if detail.get("warnings"):
        payload["warnings"] = list(detail["warnings"])
    return payload