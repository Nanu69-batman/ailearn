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
import contextlib
import contextvars
import logging
import re
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from app.ingestion import audio as audio_mod
from app.ingestion import embedder as embedder_mod
from app.ingestion import images as images_mod
from app.ingestion import visual as visual_mod
from app.ingestion import youtube as yt_mod
from app.ingestion.chunking import Chunk, TextSegment, chunk_segment
from app.ingestion.documents import extract, extract_pdf_pass
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
    #: The stage the ingest is currently in. Kept *out* of
    #: ``detail`` because the detail is the ingest report and the
    #: stage is where the job is -- two different facts about the
    #: same job, and merging them made ``detail`` untestable.
    stage: str | None = None


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


# ---------------------------------------------------------------------------
# Progress: which stage, and how far through it
# ---------------------------------------------------------------------------
#
# A 7-minute video ingest legitimately takes minutes. What made it *look*
# broken was that nothing said where it was: the payload stayed
# ``status: "processing"`` for the whole run, so transcription, visual
# description and embedding were indistinguishable from a freeze. These
# helpers record the stage and, inside the embedding stage, a running count.

#: The ingest stage names a caller may see. Stable strings: the web client
#: discriminates on them.
STAGE_EXTRACTING = "extracting"
STAGE_TRANSCRIBING = "transcribing"
STAGE_DESCRIBING = "describing"
STAGE_EMBEDDING = "embedding"


#: The job currently executing on a worker thread. Stage reporting inside
#: ``ingest_media`` has no job id of its own -- it is several call frames below
#: ``run_ingest`` -- and threading one through every ingest entry point would
#: change five public signatures for a progress string. One process-global,
#: set for the duration of ``run_ingest``, is the honest cost.
#:
#: Backed by a ContextVar (each worker thread runs in its own context via
#: ``asyncio.to_thread``), so concurrent window jobs cannot misattribute
#: stage/embedding progress. The dict-like facade keeps older callers working.
_ACTIVE_JOB_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "ingest_active_job_id", default=None
)


class _ActiveJob:
    def get(self, key: str, default=None):
        return (_ACTIVE_JOB_ID.get() or default) if key == "job_id" else default

    def __setitem__(self, key: str, value: str) -> None:
        if key == "job_id":
            _ACTIVE_JOB_ID.set(value)

    def pop(self, key: str, default=None):
        if key == "job_id":
            previous = _ACTIVE_JOB_ID.get()
            _ACTIVE_JOB_ID.set(None)
            return previous or default
        return default


_ACTIVE_JOB = _ActiveJob()


def note_stage(stage: str, **fields) -> None:
    """Record ``stage`` on the in-flight ingest job, if there is one."""
    job_id = _ACTIVE_JOB_ID.get()
    if job_id:
        set_stage(job_id, stage, **fields)


def note_progress(**fields) -> None:
    """Merge ``fields`` into the in-flight job's detail without changing stage."""
    job_id = _ACTIVE_JOB_ID.get()
    if not job_id:
        return
    try:
        with _LOCK:
            job = _JOBS.get(job_id)
            if job is not None:
                job.detail = {**(job.detail or {}), **fields}
    except Exception:  # progress is a courtesy
        log.debug("could not record progress for job %s", job_id, exc_info=True)


def set_stage(job_id: str, stage: str, **fields) -> None:
    """Record the current stage on a job. Never raises, never blocks.

    Progress reporting is a courtesy to the caller watching a spinner, so it
    must not be able to fail an ingest that is otherwise succeeding.
    """
    try:
        with _LOCK:
            job = _JOBS.get(job_id)
            if job is None:
                return
            detail = dict(job.detail or {})
            job.stage = stage
            detail.update(fields)
            job.detail = detail
    except Exception:  # see docstring
        log.debug("could not record stage %r for job %s", stage, job_id, exc_info=True)


@contextlib.contextmanager
def embedding_progress(job_id: str):
    """Publish ``embedded N/total`` on a job for the duration of a chunk write.

    Installed around the *whole* ingest rather than just the embedding call, so
    a caller polling ``GET /ingestion/jobs/{id}`` sees a counter that moves
    rather than a status stuck at ``processing``. Restores the previous sink on
    exit, including on failure, so a crashed ingest cannot leave a stale sink
    publishing into a finished job.
    """

    def _sink(done: int, total: int) -> None:
        # Resolve the job from the calling thread's context first, so a sink
        # installed by one job cannot publish into another concurrent job.
        set_stage(
            _ACTIVE_JOB_ID.get() or job_id,
            STAGE_EMBEDDING,
            embedded=done,
            embedding_total=total,
        )

    previous = embedder_mod.set_embedding_progress(_sink)
    try:
        yield
    finally:
        embedder_mod.set_embedding_progress(previous)


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


def source_type_for(filename: str, *, content_type: str | None = None) -> str:
    """Map an upload to its source type.

    ``content_type`` is a *fallback* for the one case a suffix cannot cover:
    an image uploaded with no (or a mangled) filename suffix. It is never
    used to override a suffix that resolves, so a ``.pdf`` claiming
    ``image/png`` still ingests as a pdf.
    """
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        return "pdf"
    if suffix == ".pptx":
        return "pptx"
    if suffix in VIDEO_SUFFIXES:
        return "video"
    if suffix in images_mod.IMAGE_SUFFIXES:
        return "image"
    mime = (content_type or "").split(";")[0].strip().lower()
    if mime in images_mod.IMAGE_MIME_TYPES:
        return "image"
    raise ValueError(f"unsupported file type {suffix or filename!r}")


def upload_suffix_for(
    filename: str | None, *, source_type: str, content_type: str | None = None
) -> str:
    """The staging extension for an upload.

    Normally the upload's own suffix, unchanged -- a video staged as ``.mp4``
    and a pdf staged as ``.pdf`` behave exactly as before. Only an image
    whose filename carries no suffix is given one, and it is taken from the
    declared mime type rather than guessed. An image with neither ends up
    staged extension-less, which every decoder handles by content sniffing.
    """
    suffix = Path(filename or "").suffix.lower()
    if suffix:
        return suffix
    if source_type == "image":
        mime = (content_type or "").split(";")[0].strip().lower()
        return images_mod.MIME_SUFFIX.get(mime, "")
    return suffix


def _store():
    from app.retrieval.registry import get_shared_store

    return get_shared_store()


def window_index_offset(window_start_ms: int) -> int:
    """Ordinal base for a window: ``window_start_ms // 1000 * 1000``.

    Invariant: a window ``[s, e)`` owns ordinals ``[offset, offset + (e - s))``,
    i.e. one ordinal per millisecond of window length. Windows are disjoint in
    time, so their ordinal ranges are disjoint and monotonic. Pass
    ``max_chunks=window_max_chunks(s, e)`` to enforce it.
    """
    return max(int(window_start_ms), 0) // 1000 * 1000


def window_max_chunks(window_start_ms: int, window_end_ms: int) -> int:
    """Chunk budget for a window under :func:`window_index_offset`."""
    return window_end_ms - window_index_offset(window_start_ms)


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
    index_offset: int = 0,
    max_chunks: int | None = None,
) -> list[Chunk]:
    """Assemble indexable chunks from every extractor.

    ``index_offset``/``max_chunks`` make ordinals window-safe (see
    :func:`window_index_offset`); defaults leave behaviour unchanged.

    Audio and visual chunks are tagged ``video`` so they flow through the same
    strict-T gate the store applies to transcripts. That gate is
    time-only; nothing distinguishes "spoken" from "on screen" in the
    timestamp semantics, and merging them here means one gate, not two.

    ``title`` is the source file's original name and is stamped onto every
    chunk, so ``GET /courses/{id}/resources`` reports what the learner
    uploaded rather than an internal extractor label like "transcript".
    """
    chunks: list[Chunk] = []
    index = index_offset
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
    if max_chunks is not None and index - index_offset > max_chunks:
        raise ValueError(
            f"window produced {index - index_offset} chunks, exceeding the "
            f"ordinal stride {max_chunks}; ordinals would collide with the next window"
        )
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

    note_stage(STAGE_TRANSCRIBING)
    transcript, audio_signals = audio_mod.transcribe(path, duration_ms=duration_ms)
    visual: list[visual_mod.VisualSegment] = []
    visual_report = visual_mod.VisualPassReport(duration_ms=duration_ms)
    warnings: list[str] = []
    if with_visual:
        note_stage(STAGE_DESCRIBING)
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
    # The chunk write is where the embedding happens, and on a long video it
    # is the phase a caller watches: report it so "embedding 120/253" is
    # visible rather than a frozen "processing".
    note_stage(STAGE_EMBEDDING)
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


#: Progressive ingest. A window is a whole number of visual frame intervals so
#: frame indices partition cleanly across windows.
WINDOW_MS = 120_000
INITIAL_WINDOWS = 3
LOOKAHEAD_MS = 120_000
assert WINDOW_MS % round(visual_mod.FRAME_INTERVAL_S * 1000) == 0

INGEST_MODES = ("full", "stream")


def window_bounds(duration_ms: int) -> list[tuple[int, int]]:
    """Consecutive ``[start, end)`` windows covering ``[0, duration_ms)`` exactly."""
    return [
        (s, min(s + WINDOW_MS, duration_ms)) for s in range(0, max(duration_ms, 0), WINDOW_MS)
    ]


def ingest_media_windows(
    path: str | Path,
    *,
    course_id: str,
    resource_id: str,
    video_id: str | None = None,
    with_visual: bool = True,
    max_visual_frames: int | None = None,
    title: str | None | object = _UNSET,
    mode: str = "full",
    start_window: int | None = None,
    n_windows: int | None = None,
    on_window_done=None,
) -> dict:
    """Windowed video ingest: each window is queryable as soon as it is written.

    ``mode="full"`` loops every window from ``start_window`` (default 0);
    ``mode="stream"`` does ``n_windows`` (default :data:`INITIAL_WINDOWS`) and
    leaves the resource partial. Per window: transcribe + visual over
    ``[s, e)``, build chunks with window-safe ordinals, clamp to ``e`` (never
    past the window, never past the file), stamp ``processed_through_ms`` and
    ``duration_ms``, delete any prior chunks inside ``[s, e]`` so a re-run
    leaves no stale chunks, then add. The strict ``end_ms <= T`` gate is not
    touched here. ``on_window_done(index)`` is called after each window is
    written.
    """
    if mode not in INGEST_MODES:
        raise ValueError(f"unknown ingest mode {mode!r}")
    path = Path(path)
    duration_ms = media_duration_ms(path)
    if duration_ms is None:
        raise ValueError(
            f"cannot determine duration of {path.name}; refusing to ingest "
            "time-gated content without an authoritative timeline"
        )
    resolved_title = _resolve_title(path, title)
    bounds = window_bounds(duration_ms)
    first = start_window or 0
    if n_windows is None and mode == "stream":
        n_windows = INITIAL_WINDOWS
    selected = bounds[first:] if n_windows is None else bounds[first : first + n_windows]

    store = _store()
    processed = selected[0][0] if selected else 0
    note_progress(
        mode=mode, duration_ms=duration_ms, processed_through_ms=processed,
        windows_done=0, windows_total=len(selected),
    )
    audio_chunks = visual_chunks = total_chunks = 0
    warnings: list[str] = []
    signals: dict = {}
    v_status: list[str] = []
    v_cov: list[float] = []
    v_span: list[float] = []
    v_expected = v_described = 0
    timings: list[dict] = []  # one visual-pass timing dict per window
    max_end = 0
    for done, (s, e) in enumerate(selected):
        note_stage(STAGE_TRANSCRIBING)
        transcript, window_signals = audio_mod.transcribe(
            path, duration_ms=duration_ms, start_ms=s, end_ms=e
        )
        signals = window_signals
        visual: list[visual_mod.VisualSegment] = []
        if with_visual:
            note_stage(STAGE_DESCRIBING)
            try:
                report_v = visual_mod.extract_visual_pass(
                    str(path), duration_ms=duration_ms,
                    max_frames=max_visual_frames, start_ms=s, end_ms=e,
                )
            except Exception as exc:  # noqa: BLE001 - visual is additive to audio
                log.warning("visual extraction failed for %s [%d,%d): %s", path.name, s, e, exc)
                warnings.append(f"visual extraction failed: {type(exc).__name__}: {exc}")
                report_v = visual_mod.VisualPassReport(
                    expected_indices=visual_mod.window_frame_indices(
                        duration_ms, start_ms=s, end_ms=e
                    ),
                    duration_ms=duration_ms,
                    failures=[f"{type(exc).__name__}: {exc}"],
                )
            visual = report_v.segments
            warnings.extend(report_v.warnings)
            v_status.append(report_v.status)
            v_cov.append(report_v.coverage_pct)
            v_span.append(report_v.span_pct)
            v_expected += len(report_v.expected_indices)
            v_described += len(report_v.covered_indices)
            if report_v.timings:
                timings.append(report_v.timings)

        chunks = build_chunks(
            resource_id=resource_id, course_id=course_id, source_type="video",
            transcript=transcript, visual=visual, video_id=video_id,
            title=resolved_title, index_offset=window_index_offset(s),
            max_chunks=window_max_chunks(s, e),
        )
        chunks = _clamp_to_duration(chunks, duration_ms)
        bounded: list[Chunk] = []
        for chunk in chunks:
            # A window never claims content beyond its own end.
            if chunk.start_ms is not None and chunk.start_ms >= e:
                continue
            if chunk.end_ms is not None and chunk.end_ms > e:
                chunk.end_ms = e
            if (
                chunk.start_ms is not None
                and chunk.end_ms is not None
                and chunk.end_ms <= chunk.start_ms
            ):
                continue
            chunk.metadata["processed_through_ms"] = min(e, duration_ms)
            chunk.metadata["duration_ms"] = duration_ms
            bounded.append(chunk)
        if not bounded:
            log.warning("window [%d,%d) of %s produced no chunks", s, e, resource_id)
        note_stage(STAGE_EMBEDDING)
        store.delete_resource_range(resource_id, s, e)
        written = store.add(bounded)
        total_chunks += written
        audio_chunks += sum(1 for c in bounded if c.metadata.get("kind") == "transcript")
        visual_chunks += sum(1 for c in bounded if c.metadata.get("kind") == "visual")
        max_end = max([max_end, *(c.end_ms or 0 for c in bounded)])
        processed = min(e, duration_ms)
        note_progress(
            mode=mode, duration_ms=duration_ms, processed_through_ms=processed,
            windows_done=done + 1, windows_total=len(selected),
        )
        if on_window_done is not None:
            try:
                on_window_done(first + done)
            except Exception:
                log.warning("on_window_done failed", exc_info=True)

    if not with_visual:
        visual_status = "skipped"
    else:
        visual_status = (
            "complete" if all(x == "complete" for x in v_status)
            else next(x for x in v_status if x != "complete")
        )
    return {
        "mode": mode,
        "processed_through_ms": processed,
        "windows_done": len(selected),
        "windows_total": len(selected),
        "audio_chunks": audio_chunks,
        "visual_chunks": visual_chunks,
        "total_chunks": total_chunks,
        "duration_ms": duration_ms,
        "max_end_ms": max_end,
        "audio_signals": signals,
        "visual_status": visual_status,
        "visual_expected_frames": v_expected,
        "visual_described_frames": v_described,
        "visual_coverage_pct": round(sum(v_cov) / len(v_cov), 1) if v_cov else 0.0,
        "visual_span_pct": round(sum(v_span) / len(v_span), 1) if v_span else 0.0,
        "warnings": warnings,
        "timings": timings,
    }


def ingest_document(
    path: str | Path,
    *,
    course_id: str,
    resource_id: str,
    source_type: str,
    title: str | None | object = _UNSET,
) -> dict:
    """Document ingest. Never time-gated, so no duration lookup happens.

    ``title`` follows the same rule as :func:`ingest_media`: it is the name
    the learner uploaded, passed explicitly by upload callers because
    ``path`` is the staged ``{resource_id}.{ext}`` asset.

    A PDF goes through the *report* form of extraction, not the segments
    form, because a document that could not be read has to be reported as
    such: "no text layer and no OCR" is a failure, and it must not arrive
    as a cheerful zero-chunk success. The report is carried onto the
    payload so the gap reaches the job record instead of a log line.
    """
    path = Path(path)
    report = None
    if source_type == "pdf":
        report = extract_pdf_pass(path)
        segments = report.segments
    else:
        segments = extract(path, source_type)
    chunks = build_chunks(
        resource_id=resource_id,
        course_id=course_id,
        source_type=source_type,
        text_segments=segments,
        title=_resolve_title(path, title),
    )
    written = _store().add(chunks)
    out = {"document_chunks": written, "total_chunks": written}
    if report is not None:
        out["document_status"] = report.status
        out["text_chars"] = report.text_chars
        out["ocr_status"] = report.ocr.status
        out["ocr_reason"] = report.ocr.reason
        out["warnings"] = list(report.warnings)
    return out


def ingest_image(
    path: str | Path,
    *,
    course_id: str,
    resource_id: str,
    image_no: int = images_mod.DEFAULT_IMAGE_NO,
    title: str | None | object = _UNSET,
) -> dict:
    """Describe an image with the video visual pass's vision call and index it.

    Images are ungated, exactly like pdf/pptx: an uploaded diagram is visible
    to the learner at every T, and there is no timeline to compute a bound
    from. So the chunk carries ``image_no`` -- a stable 1-based identity a
    citation can point at -- and **no** ``start_ms``/``end_ms``. Adding it to
    ``TIME_GATED_SOURCE_TYPES`` would be the bug here, not the omission.

    ``title`` follows the same rule as :func:`ingest_media`: the learner's
    original filename, passed explicitly because ``path`` is the staged asset.

    Raises ``images_mod.ImageDescriptionError`` when the image cannot be
    described. That propagates to ``run_ingest``, which records
    ``status: "failed"`` -- the resource indexes nothing rather than carrying
    invented text.
    """
    path = Path(path)
    described = images_mod.describe_image(path, image_no=image_no)
    chunks = build_chunks(
        resource_id=resource_id,
        course_id=course_id,
        source_type="image",
        # No start_ms/end_ms: an image is ungated (see the docstring), so it
        # takes exactly the document branch of the splitter and is split for
        # size only. ``kind="visual"`` records that the evidence came from the
        # vision model, which is what ``normalize_modality`` maps to
        # "visual" -- the same value a sampled video frame carries.
        text_segments=[TextSegment(text=described.text, kind="visual")],
        title=_resolve_title(path, title),
    )
    for chunk in chunks:
        # Belt and braces. An image has no timeline, so a non-null bound here
        # would be a fabricated citation target. Asserted rather than trusted.
        assert chunk.start_ms is None and chunk.end_ms is None, (
            f"image chunk {chunk.chunk_id} carries a timestamp; an image is "
            "ungated and has no moment in time to point at"
        )
        # Stamped post-chunking on purpose: ``app.ingestion.chunking`` is owned
        # by another change, so the image number rides in metadata rather than
        # in a new ``Chunk`` field. It does not feed ``chunk_id_for``, so id
        # stability is unaffected.
        chunk.metadata["image_no"] = described.image_no
    written = _store().add(chunks)
    return {
        "image_chunks": written,
        "total_chunks": written,
        "image_no": described.image_no,
        "source": "vision",
        "warnings": list(described.warnings),
    }


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
    """Execute ``work()`` on a worker thread, recording the outcome on the job.

    Stage progress is published throughout, not only at the end. The embedder
    reports ``embedded N/total`` as it goes, so a caller polling
    ``GET /ingestion/jobs/{id}`` sees a moving counter during the (sometimes
    multi-minute) chunk write instead of an undifferentiated ``processing``.
    """
    update_job(job.job_id, status="processing", error=None)
    set_stage(job.job_id, STAGE_EXTRACTING)
    _ACTIVE_JOB["job_id"] = job.job_id
    try:
        with embedding_progress(job.job_id):
            report = work()
    except Exception as exc:
        log.exception("ingest job %s failed", job.job_id)
        update_job(
            job.job_id,
            status="failed",
            error=f"{type(exc).__name__}: {exc}",
        )
        return
    finally:
        _ACTIVE_JOB.pop("job_id", None)
    # Merge rather than replace: the stage/counter fields describe how the
    # ingest got there, and replacing them with the report alone would erase
    # the only evidence of where the time went.
    merged = {**(job.detail or {}), **(report or {})}
    update_job(job.job_id, status="done", detail=merged)


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
    for key in ("visual_status", "visual_coverage_pct", "visual_span_pct", "image_no"):
        if key in detail:
            payload[key] = detail[key]
    # The document pass report. A PDF with no text layer and no OCR is a
    # failed read, and the job record has to say so -- the same reason the
    # visual status is carried above.
    for key in ("document_status", "text_chars", "ocr_status", "ocr_reason"):
        if key in detail:
            payload[key] = detail[key]
    # Stage + the embedding counter. Additive and omitted unless the ingest
    # actually recorded them, so the frozen four-key contract is untouched.
    if job.stage is not None:
        payload["stage"] = job.stage
    for key in ("embedded", "embedding_total"):
        if detail.get(key) is not None:
            payload[key] = detail[key]
    for key in (
        "mode", "processed_through_ms", "duration_ms", "windows_done", "windows_total"
    ):
        if detail.get(key) is not None:
            payload[key] = detail[key]
    if detail.get("warnings"):
        payload["warnings"] = list(detail["warnings"])
    return payload