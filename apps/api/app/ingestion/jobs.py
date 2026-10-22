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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from app import devtrace
from app.ingestion import audio as audio_mod
from app.ingestion import embedder as embedder_mod
from app.ingestion import images as images_mod
from app.ingestion import visual as visual_mod
from app.ingestion import youtube as yt_mod
from app.ingestion.chunking import CHUNK_CHARS, Chunk, TextSegment, chunk_segment
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
    #: What the developer view shows about this job (file, type, size).
    trace: dict = field(default_factory=dict)


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
        devtrace.emit("embed", "progress", title=f"Embedded {done}/{total}",
                      at=devtrace.where("embed"), data={"done": done, "total": total})

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


#: Consecutive speech cues (transcript or caption) are merged into one chunk
#: spanning at most this long. A cue is a sentence fragment -- 38 to 118 chars
#: on average -- and every chunk costs one embedding, so one chunk per cue
#: spent ~8x the embedding quota for passages too short to retrieve well.
SPEECH_GROUP_MS = 30_000
#: Consecutive frame descriptions merged into one chunk: three 4 s frames.
VISUAL_GROUP_MS = 12_000


def group_timed(segments: list[TextSegment], *, max_span_ms: int) -> list[TextSegment]:
    """Merge consecutive time-bounded segments of one kind into bigger ones.

    A group spans ``[first.start_ms, max(end_ms)]`` and holds at most
    :data:`CHUNK_CHARS` characters, so it still becomes a single chunk. The
    merged ``end_ms`` is the latest end in the group: the strict-T gate then
    admits the passage only once ALL of it has played, never earlier.
    Segments without both bounds (documents, unknown ends) are never merged;
    they pass through unchanged, so a NULL ``end_ms`` stays NULL and excluded.
    """
    out: list[TextSegment] = []
    group: list[TextSegment] = []

    def flush() -> None:
        if not group:
            return
        if len(group) == 1:
            out.append(group[0])
        else:
            out.append(
                TextSegment(
                    text=" ".join(s.text.strip() for s in group),
                    start_ms=group[0].start_ms,
                    end_ms=max(s.end_ms for s in group),
                    kind=group[0].kind,
                )
            )
        group.clear()

    for seg in segments:
        timed = seg.start_ms is not None and seg.end_ms is not None
        if not timed or seg.page_no is not None or seg.slide_no is not None:
            flush()
            out.append(seg)
            continue
        if group and (
            seg.kind != group[0].kind
            or seg.end_ms - group[0].start_ms > max_span_ms
            or sum(len(s.text) + 1 for s in group) + len(seg.text) > CHUNK_CHARS
        ):
            flush()
        group.append(seg)
    flush()
    return out


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
    text_segments = group_timed(text_segments or [], max_span_ms=SPEECH_GROUP_MS)
    transcript_segments = group_timed(
        [
            TextSegment(text=cue.text, start_ms=cue.start_ms, end_ms=cue.end_ms,
                        kind="transcript")
            for cue in transcript or []
        ],
        max_span_ms=SPEECH_GROUP_MS,
    )
    visual_segments = group_timed(
        [
            TextSegment(text=frame.text, start_ms=frame.start_ms,
                        end_ms=frame.end_ms, kind="visual")
            for frame in visual or []
        ],
        max_span_ms=VISUAL_GROUP_MS,
    )
    for seg in text_segments:
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

    for seg in [*transcript_segments, *visual_segments]:
        made = chunk_segment(
            seg,
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
    _trace_chunks(chunks)
    return chunks


def _segments_info(segments, kind: str) -> dict:
    """A trace summary of transcript or frame segments: count, span, samples."""
    segs = list(segments or [])
    return {
        "kind": kind,
        "segments": len(segs),
        "span_ms": [
            min((s.start_ms for s in segs if s.start_ms is not None), default=None),
            max((s.end_ms for s in segs if s.end_ms is not None), default=None),
        ],
        "samples": [
            {"start_ms": s.start_ms, "end_ms": s.end_ms, "text": devtrace.preview(s.text, 140)}
            for s in segs[:3]
        ],
    }


def _trace_chunks(chunks: list[Chunk]) -> None:
    kinds: dict[str, int] = {}
    for c in chunks:
        k = c.metadata.get("kind") or c.source_type
        kinds[k] = kinds.get(k, 0) + 1
    devtrace.emit(
        "chunk", "end", title=f"Split into {len(chunks)} chunks",
        data={
            "chunks": len(chunks),
            "by_kind": kinds,
            "max_chars": CHUNK_CHARS,
            "samples": [
                {
                    "chunk_id": c.chunk_id,
                    "kind": c.metadata.get("kind") or c.source_type,
                    "start_ms": c.start_ms,
                    "end_ms": c.end_ms,
                    "page_no": c.page_no,
                    "text": devtrace.preview(c.text, 160),
                }
                for c in chunks[:3]
            ],
        },
    )


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

    devtrace.emit("extract", "end", title="Read the video container",
                  data={"duration_ms": duration_ms, "file": path.suffix})
    note_stage(STAGE_TRANSCRIBING)
    with devtrace.span("transcribe", title="Transcribe the audio",
                       at=devtrace.where("asr"), data={"range_ms": [0, duration_ms]}) as out:
        transcript, audio_signals = audio_mod.transcribe(path, duration_ms=duration_ms)
        out.update(_segments_info(transcript, "transcript"))
    visual: list[visual_mod.VisualSegment] = []
    visual_report = visual_mod.VisualPassReport(duration_ms=duration_ms)
    warnings: list[str] = []
    if with_visual:
        note_stage(STAGE_DESCRIBING)
        try:
            with devtrace.span(
                "describe", title="Describe sampled frames", at=devtrace.where("vision"),
                data={"every_s": visual_mod.FRAME_INTERVAL_S, "range_ms": [0, duration_ms]},
            ) as out:
                visual_report = visual_mod.extract_visual_pass(
                    str(path), duration_ms=duration_ms, max_frames=max_visual_frames
                )
                out.update(_segments_info(visual_report.segments, "visual"))
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
#: Stream mode processes this many windows at upload (one = the first two
#: minutes); the player's position requests queue the rest as it plays.
INITIAL_WINDOWS = 1
LOOKAHEAD_MS = 2 * WINDOW_MS
assert WINDOW_MS % round(visual_mod.FRAME_INTERVAL_S * 1000) == 0

INGEST_MODES = ("full", "stream")

KIND_TRANSCRIPT = "transcript"
KIND_VISUAL = "visual"
ALL_STAGES = (KIND_TRANSCRIPT, KIND_VISUAL)

#: Ordinal budget of ONE stage inside a window. A window ``[s, e)`` owns the
#: ordinals ``[offset, offset + WINDOW_MS)`` (``offset = window_index_offset(s)``).
#: Transcript chunks use ``[offset, offset + SPAN)`` and visual chunks use
#: ``[offset + SPAN, offset + 2 * SPAN)`` with ``SPAN = WINDOW_MS // 2``. The
#: two stages therefore never collide and each is deterministic on its own, so
#: either stage can be re-run independently. ``build_chunks(max_chunks=SPAN)``
#: raises if a stage overflows its sub-range.
STAGE_ORDINAL_SPAN = WINDOW_MS // 2


def stage_index_offset(window_start_ms: int, kind: str) -> int:
    base = window_index_offset(window_start_ms)
    return base if kind == KIND_TRANSCRIPT else base + STAGE_ORDINAL_SPAN


def window_bounds(duration_ms: int) -> list[tuple[int, int]]:
    """Consecutive ``[start, end)`` windows covering ``[0, duration_ms)`` exactly."""
    return [
        (s, min(s + WINDOW_MS, duration_ms)) for s in range(0, max(duration_ms, 0), WINDOW_MS)
    ]


@dataclass
class _WinCtx:
    path: Path
    course_id: str
    resource_id: str
    video_id: str | None
    title: str | None
    duration_ms: int
    with_visual: bool
    max_visual_frames: int | None
    store: object


def _bound_chunks(
    chunks: list[Chunk], ctx: _WinCtx, s: int, e: int, extra: dict
) -> list[Chunk]:
    """Clamp to the file and the window, then stamp the frontier markers."""
    chunks = _clamp_to_duration(chunks, ctx.duration_ms)
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
        chunk.metadata["processed_through_ms"] = min(e, ctx.duration_ms)
        chunk.metadata["duration_ms"] = ctx.duration_ms
        chunk.metadata.update(extra)
        bounded.append(chunk)
    return bounded


def _compute_transcript(ctx: _WinCtx, s: int, e: int) -> tuple[list[Chunk], dict]:
    note_stage(STAGE_TRANSCRIBING)
    with devtrace.span("transcribe", title="Transcribe the audio",
                       at=devtrace.where("asr"), data={"range_ms": [s, e]}) as out:
        transcript, signals = audio_mod.transcribe(
            ctx.path, duration_ms=ctx.duration_ms, start_ms=s, end_ms=e
        )
        out.update(_segments_info(transcript, "transcript"))
    chunks = build_chunks(
        resource_id=ctx.resource_id, course_id=ctx.course_id, source_type="video",
        transcript=transcript, video_id=ctx.video_id, title=ctx.title,
        index_offset=stage_index_offset(s, KIND_TRANSCRIPT),
        max_chunks=STAGE_ORDINAL_SPAN,
    )
    # Without a visual pass there is nothing to wait for: the visual frontier
    # equals the transcript frontier. With one, stamp 0 so the resource carries
    # the marker (is not legacy) from the first transcript commit; the max over
    # chunks is then raised by the visual chunks as stage V lands.
    extra = {"visual_through_ms": min(e, ctx.duration_ms) if not ctx.with_visual else 0}
    return _bound_chunks(chunks, ctx, s, e, extra), signals


def _compute_visual(ctx: _WinCtx, s: int, e: int):
    note_stage(STAGE_DESCRIBING)
    with devtrace.span(
        "describe", title="Describe sampled frames", at=devtrace.where("vision"),
        data={"every_s": visual_mod.FRAME_INTERVAL_S, "range_ms": [s, e]},
    ) as out:
        report_v = visual_mod.extract_visual_pass(
            str(ctx.path), duration_ms=ctx.duration_ms,
            max_frames=ctx.max_visual_frames, start_ms=s, end_ms=e,
        )
        out.update(_segments_info(report_v.segments, "visual"))
    chunks = build_chunks(
        resource_id=ctx.resource_id, course_id=ctx.course_id, source_type="video",
        visual=report_v.segments, video_id=ctx.video_id, title=ctx.title,
        index_offset=stage_index_offset(s, KIND_VISUAL),
        max_chunks=STAGE_ORDINAL_SPAN,
    )
    extra = {"visual_through_ms": min(e, ctx.duration_ms)}
    return _bound_chunks(chunks, ctx, s, e, extra), report_v


def _commit_stage(ctx: _WinCtx, kind: str, s: int, e: int, chunks: list[Chunk]) -> int:
    """Replace this stage's chunks in ``[s, e]`` and write the new ones."""
    note_stage(STAGE_EMBEDDING)
    ctx.store.delete_resource_range(ctx.resource_id, s, e, kind=kind)
    if not chunks:
        _mark_empty_window(ctx, kind, s, e)
        return 0
    return ctx.store.add(chunks)


def _mark_empty_window(ctx: _WinCtx, kind: str, s: int, e: int) -> None:
    """Advance the frontier past a window with nothing to index."""
    mark = getattr(ctx.store, "mark_progress", None)
    if mark is None:
        return
    end = min(e, ctx.duration_ms)
    if kind == KIND_TRANSCRIPT:
        visual = 0 if ctx.with_visual else end
    else:
        visual = end
    mark(
        resource_id=ctx.resource_id, course_id=ctx.course_id, kind=kind, start_ms=s,
        frontier={
            "processed_through_ms": end,
            "duration_ms": ctx.duration_ms,
            "visual_through_ms": visual,
        },
    )


class _Tally:
    """Thread-safe aggregation of per-stage results and job progress."""

    def __init__(self, *, mode, duration_ms, stages, total, first_start, with_visual):
        self.lock = threading.Lock()
        self.mode, self.duration_ms, self.stages, self.total = mode, duration_ms, stages, total
        self.with_visual = with_visual
        self.t_done = self.v_done = 0
        self.processed = self.visual = first_start
        self.audio_chunks = self.visual_chunks = self.total_chunks = 0
        self.warnings: list[str] = []
        self.signals: dict = {}
        self.v_status: list[str] = []
        self.v_cov: list[float] = []
        self.v_span: list[float] = []
        self.v_expected = self.v_described = 0
        self.timings: list[dict] = []
        self.max_end = 0

    def progress_fields(self) -> dict:
        active = [
            n for k, n in ((KIND_TRANSCRIPT, self.t_done), (KIND_VISUAL, self.v_done))
            if k in self.stages
        ]
        out = {
            "mode": self.mode, "duration_ms": self.duration_ms,
            "windows_done": min(active) if active else 0, "windows_total": self.total,
        }
        if KIND_TRANSCRIPT in self.stages:
            out["processed_through_ms"] = self.processed
            out["transcript_windows_done"] = self.t_done
        if KIND_VISUAL in self.stages or (
            KIND_TRANSCRIPT in self.stages and not self.with_visual
        ):
            out["visual_through_ms"] = self.visual
        if KIND_VISUAL in self.stages:
            out["visual_windows_done"] = self.v_done
        return out

    def record(self, kind, e, chunks, written, extra=None) -> None:
        with self.lock:
            self.total_chunks += written
            self.max_end = max([self.max_end, *(c.end_ms or 0 for c in chunks)])
            end = min(e, self.duration_ms)
            if kind == KIND_TRANSCRIPT:
                self.t_done += 1
                self.processed = end
                self.audio_chunks += sum(
                    1 for c in chunks if c.metadata.get("kind") == "transcript"
                )
                if not self.with_visual:
                    self.visual = end
                if extra:
                    self.signals = extra
            else:
                self.v_done += 1
                self.visual = end
                self.visual_chunks += sum(
                    1 for c in chunks if c.metadata.get("kind") == "visual"
                )
                if extra is not None:
                    self.warnings.extend(extra.warnings)
                    self.v_status.append(extra.status)
                    self.v_cov.append(extra.coverage_pct)
                    self.v_span.append(extra.span_pct)
                    self.v_expected += len(extra.expected_indices)
                    self.v_described += len(extra.covered_indices)
                    if extra.timings:
                        self.timings.append(extra.timings)
            fields = self.progress_fields()
        note_progress(**fields)


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
    stages: tuple[str, ...] = ALL_STAGES,
    pipelined: bool = True,
) -> dict:
    """Windowed, two-stage video ingest.

    Each window is split into stage T (transcribe -> transcript chunks) and
    stage V (frame description -> visual chunks), committed independently: the
    transcript is queryable as soon as stage T lands. ``processed_through_ms``
    is the transcript frontier, ``visual_through_ms`` the visual one (stamped
    on visual chunks; on transcript chunks only when ``with_visual`` is False).

    ``mode="full"`` loops every window from ``start_window`` (default 0);
    ``mode="stream"`` does ``n_windows`` (default :data:`INITIAL_WINDOWS`).
    ``stages`` selects which stages run (default both). With both stages and
    ``pipelined=True`` a transcript lane and a vision lane run on two threads
    (concurrency 1 each; V of window N only starts after T of window N has
    committed), and each lane's commit (embedding + write) overlaps its next
    compute. The resulting chunk set is identical to serial execution. Any
    exception in either lane fails the whole call with the stage and window in
    the message; the other lane stops at its next window boundary.

    ``on_window_done(index)`` fires after a window's stage T commit (or stage V
    when only V runs). The strict ``end_ms <= T`` gate is not touched here.
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
    run_stages = tuple(k for k in ALL_STAGES if k in stages)
    if KIND_VISUAL in run_stages and not with_visual:
        run_stages = tuple(k for k in run_stages if k != KIND_VISUAL)
    if not run_stages:
        raise ValueError("no ingest stage selected")
    ctx = _WinCtx(
        path=path, course_id=course_id, resource_id=resource_id, video_id=video_id,
        title=_resolve_title(path, title), duration_ms=duration_ms,
        with_visual=with_visual, max_visual_frames=max_visual_frames, store=_store(),
    )
    bounds = window_bounds(duration_ms)
    first = start_window or 0
    if n_windows is None and mode == "stream":
        n_windows = INITIAL_WINDOWS
    selected = bounds[first:] if n_windows is None else bounds[first : first + n_windows]
    devtrace.emit(
        "extract", "end", title="Read the video and plan windows",
        data={
            "duration_ms": duration_ms,
            "mode": mode,
            "window_ms": WINDOW_MS,
            "windows": [list(b) for b in selected][:8],
            "stages": list(run_stages),
        },
    )

    tally = _Tally(
        mode=mode, duration_ms=duration_ms, stages=run_stages, total=len(selected),
        first_start=selected[0][0] if selected else 0, with_visual=with_visual,
    )
    note_progress(**tally.progress_fields())

    done_events = [threading.Event() for _ in selected]
    stop = threading.Event()
    errors: list[tuple[str, int, int, int, BaseException]] = []
    err_lock = threading.Lock()

    def fail(kind: str, i: int, s: int, e: int, exc: BaseException) -> None:
        log.error("%s stage failed for %s window %d [%d,%d): %s",
                  kind, resource_id, first + i, s, e, exc, exc_info=exc)
        with err_lock:
            errors.append((kind, first + i, s, e, exc))
        stop.set()

    def compute(kind: str, s: int, e: int):
        if kind == KIND_TRANSCRIPT:
            chunks, signals = _compute_transcript(ctx, s, e)
            return chunks, signals
        return _compute_visual(ctx, s, e)

    def commit(kind: str, i: int, s: int, e: int, chunks, extra) -> None:
        try:
            if not chunks:
                log.warning("%s window [%d,%d) of %s produced no chunks", kind, s, e, resource_id)
            written = _commit_stage(ctx, kind, s, e, chunks)
            tally.record(kind, e, chunks, written, extra)
            if kind == KIND_TRANSCRIPT:
                done_events[i].set()
            notify = kind == run_stages[0]
            if notify and on_window_done is not None:
                try:
                    on_window_done(first + i)
                except Exception:
                    log.warning("on_window_done failed", exc_info=True)
        except Exception as exc:  # noqa: BLE001 - recorded, re-raised by the caller
            fail(kind, i, s, e, exc)

    def lane(kind: str) -> None:
        pool = ThreadPoolExecutor(max_workers=1)
        pending = None
        try:
            for i, (s, e) in enumerate(selected):
                if stop.is_set():
                    break
                if kind == KIND_VISUAL and KIND_TRANSCRIPT in run_stages:
                    while not done_events[i].wait(0.05):
                        if stop.is_set():
                            return
                    if stop.is_set():
                        return
                try:
                    chunks, extra = compute(kind, s, e)
                except Exception as exc:  # noqa: BLE001
                    fail(kind, i, s, e, exc)
                    break
                if pending is not None:
                    pending.result()
                pending = pool.submit(
                    contextvars.copy_context().run, commit, kind, i, s, e, chunks, extra
                )
            if pending is not None:
                pending.result()
        finally:
            pool.shutdown(wait=True)

    if pipelined and len(run_stages) == 2:
        with ThreadPoolExecutor(max_workers=2) as lanes:
            # Copy the context HERE, on the calling thread, one copy per lane:
            # a copy taken inside the pool thread is that thread's own empty
            # context, which lost the active job (stage progress) and the
            # developer-view run (every transcribe/describe/embed step).
            futures = [
                lanes.submit(contextvars.copy_context().run, lane, k) for k in run_stages
            ]
            for f in futures:
                f.result()
    else:
        for i, (s, e) in enumerate(selected):
            for kind in run_stages:
                if stop.is_set():
                    break
                try:
                    chunks, extra = compute(kind, s, e)
                except Exception as exc:  # noqa: BLE001
                    fail(kind, i, s, e, exc)
                    break
                commit(kind, i, s, e, chunks, extra)
            if stop.is_set():
                break

    if errors:
        kind, widx, s, e, exc = errors[0]
        raise RuntimeError(
            f"{kind} stage failed for window {widx} [{s},{e}): "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    if not with_visual:
        visual_status = "skipped"
    elif not tally.v_status:
        visual_status = "skipped" if KIND_VISUAL not in run_stages else "complete"
    else:
        visual_status = (
            "complete" if all(x == "complete" for x in tally.v_status)
            else next(x for x in tally.v_status if x != "complete")
        )
    report = {
        "mode": mode,
        "windows_done": len(selected),
        "windows_total": len(selected),
        "transcript_windows_done": tally.t_done,
        "visual_windows_done": tally.v_done,
        "audio_chunks": tally.audio_chunks,
        "visual_chunks": tally.visual_chunks,
        "total_chunks": tally.total_chunks,
        "duration_ms": duration_ms,
        "max_end_ms": tally.max_end,
        "audio_signals": tally.signals,
        "visual_status": visual_status,
        "visual_expected_frames": tally.v_expected,
        "visual_described_frames": tally.v_described,
        "visual_coverage_pct": (
            round(sum(tally.v_cov) / len(tally.v_cov), 1) if tally.v_cov else 0.0
        ),
        "visual_span_pct": (
            round(sum(tally.v_span) / len(tally.v_span), 1) if tally.v_span else 0.0
        ),
        "warnings": tally.warnings,
        "timings": tally.timings,
    }
    if KIND_TRANSCRIPT in run_stages:
        report["processed_through_ms"] = tally.processed
    if KIND_VISUAL in run_stages or not with_visual:
        report["visual_through_ms"] = tally.visual
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
    with devtrace.span("extract", title=f"Extract text from the {source_type}",
                       data={"source_type": source_type}) as out:
        if source_type == "pdf":
            report = extract_pdf_pass(path)
            segments = report.segments
            out["text_chars"] = report.text_chars
            out["ocr"] = report.ocr.status
        else:
            segments = extract(path, source_type)
        out["segments"] = len(segments)
        out["pages"] = len({getattr(sg, "page_no", None) for sg in segments} - {None})
        out["samples"] = [
            {"page_no": getattr(sg, "page_no", None), "text": devtrace.preview(sg.text, 140)}
            for sg in segments[:2]
        ]
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
    token = devtrace.start_run(
        "ingest", job.course_id, run_id=job.job_id, title="File received by the API",
        data={"job_id": job.job_id, "resource_id": job.resource_id, **(job.trace or {})},
    )
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
        devtrace.end_run(token, "error", title="Ingest failed",
                         data={"error": type(exc).__name__})
        return
    finally:
        _ACTIVE_JOB.pop("job_id", None)
    devtrace.end_run(
        token, title="Indexed and ready to ask",
        data={k: v for k, v in (report or {}).items()
              if isinstance(v, (int, float, str, bool)) or v is None},
    )
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
        "mode", "processed_through_ms", "visual_through_ms", "duration_ms",
        "windows_done", "windows_total", "transcript_windows_done",
        "visual_windows_done",
    ):
        if detail.get(key) is not None:
            payload[key] = detail[key]
    if detail.get("warnings"):
        payload["warnings"] = list(detail["warnings"])
    return payload