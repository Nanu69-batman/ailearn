"""Ingestion routes. Response shapes are frozen by ``apps/web/lib/api.ts``.

    POST /courses/{course_id}/ingest          multipart file upload
                                              (video/pdf/pptx, or an image,
                                              optional `subject_id` form field)
    POST /courses/{course_id}/ingest-youtube  {"url": "..."}
    GET  /ingestion/jobs/{job_id}
    GET  /courses/{course_id}/resources
    GET  /resources/{resource_id}/media       (streams a downloaded asset)

``apps/web`` is never modified by the API; these shapes exist to match it.
"""

from __future__ import annotations

import logging
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.config import settings
from app.ingestion.jobs import (
    INGEST_MODES,
    LOOKAHEAD_MS,
    MAX_UPLOAD_BYTES,
    WINDOW_MS,
    get_job,
    ingest_document,
    ingest_image,
    ingest_media_windows,
    ingest_youtube,
    job_payload,
    media_duration_ms,
    new_job,
    sanitize_filename,
    schedule,
    source_type_for,
    upload_suffix_for,
)

log = logging.getLogger(__name__)

router = APIRouter()

#: Uploads land here and are kept: re-ingest and /media streaming both need
#: the original bytes, and Chroma stores only the derived text.
UPLOAD_DIR = Path(settings.yt_data_dir).parent / "uploads"

#: Resource ids are minted as ``res_<hex>`` (see :func:`_resource_id`). A
#: staged asset is ``{resource_id}.{suffix}``, so this is the whole grammar of
#: a filename we are ever willing to name for deletion. Anything with a dot, a
#: separator, or ``..`` in it is not one of ours, and must never reach
#: ``glob()`` or ``unlink()``.
_SAFE_RESOURCE_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")

#: Upload timestamps for ``settings.max_uploads_per_hour``. Global, not per
#: IP: behind a hosting proxy every client shares one address, and the thing
#: being protected (the model quota) is global too.
_UPLOAD_TIMES: list[float] = []
_UPLOAD_LOCK = threading.Lock()


def _take_upload_slot() -> None:
    cap = settings.max_uploads_per_hour
    if cap <= 0:
        return
    now = time.monotonic()
    with _UPLOAD_LOCK:
        _UPLOAD_TIMES[:] = [t for t in _UPLOAD_TIMES if now - t < 3600]
        if len(_UPLOAD_TIMES) >= cap:
            raise HTTPException(
                status_code=429,
                detail=(
                    f"this deployment accepts {cap} uploads per hour to stay within "
                    "its free model quota; please try again later"
                ),
            )
        _UPLOAD_TIMES.append(now)


def reset_upload_slots() -> None:
    with _UPLOAD_LOCK:
        _UPLOAD_TIMES.clear()


def resolve_asset(
    resource_id: str, *, upload_dir: Path | None = None
) -> Path | None:
    """Return the staged ``{resource_id}.{suffix}`` asset, or ``None``.

    This is the *single* definition of "which file is this resource's media".
    ``GET /resources/{id}/media``, ``GET /courses/{id}/resources`` and the
    purge path all resolve through it, so a delete can never target a
    different file than the one the media endpoint would serve.

    Deliberately narrow. No globbing across resources, no prefix matching, no
    directory sweep, no suffix guessing: exactly one name shape qualifies, the
    suffix must be a single real extension, and the result must sit directly in
    the uploads directory. A non-generated id -- including a traversal-shaped
    one -- resolves to ``None``, which callers treat as "no asset".

    ``upload_dir`` is resolved from the module global at call time rather than
    bound as a default argument, so tests that retarget ``UPLOAD_DIR`` are
    honoured.
    """
    if not resource_id or not _SAFE_RESOURCE_ID.fullmatch(resource_id):
        return None

    base = Path(upload_dir) if upload_dir is not None else UPLOAD_DIR
    try:
        matches = sorted(base.glob(f"{resource_id}.*"))
    except (OSError, ValueError):
        # A malformed pattern or an unreadable directory is "no asset", never
        # an error and never a reason to fall back to a broader search.
        return None

    try:
        base_resolved = base.resolve()
    except OSError:
        return None

    for path in matches:
        # Exact staged name: `{resource_id}` + one extension. A doubled suffix
        # (`res_x.tar.gz`) is not something ingestion ever writes, so it is not
        # a file this resource is entitled to.
        if not path.is_file():
            continue
        if path.name != f"{resource_id}{path.suffix}" or not path.suffix:
            continue
        try:
            # Belt and braces against traversal/symlink escape: the file must
            # live directly in the uploads dir, not one level above it.
            if path.parent.resolve() != base_resolved:
                continue
        except OSError:
            continue
        return path
    return None


class YouTubeIngestRequest(BaseModel):
    url: str = Field(min_length=1)
    #: Optional subject scoping. When present, the resource is ingested under
    #: that subject's real ``course_id`` (derived here, not taken from the
    #: path) and auto-attached to the subject, so it shows up in
    #: ``GET /subjects/{id}/resources`` immediately.
    subject_id: str | None = None


def _resource_id() -> str:
    return f"res_{uuid.uuid4().hex[:12]}"


def _resolve_ingest_subject(
    course_id: str, subject_id: str | None
) -> tuple[str, object | None]:
    """Validate ``subject_id``; return ``(target_course_id, subject_or_None)``.

    Deliberately the *same* validation, in the same order and with the same
    status codes, that ``POST /courses/{course_id}/ingest-youtube`` applies:
    an unknown subject is 404, a subject with no course is 422, and the
    resource is attached through ``subject_store.attach_resource``, which
    itself refuses an id the retrieval registry cannot vouch for
    (``UnknownResourceError``). There is exactly one validation path, so file
    upload cannot drift from YouTube ingest.

    With no ``subject_id`` the path ``course_id`` is returned untouched and
    the subject is ``None``, so omitting the field is a genuine no-op.
    """
    if subject_id is None:
        return course_id, None
    from app.subjects.store import subject_store

    subject = subject_store.get(subject_id)
    if subject is None:
        raise HTTPException(
            status_code=404, detail=f"unknown subject {subject_id}"
        )
    if not subject.course_id:
        raise HTTPException(
            status_code=422,
            detail=f"subject {subject_id} is not linked to a course",
        )
    # The subject owns the real course; the path id is only a route hint.
    return subject.course_id, subject


def _attach_to_subject(subject, resource_id: str) -> None:
    """Auto-attach an ingested resource, exactly as the YouTube route does.

    Attach failure is logged, never raised: the resource IS indexed, and
    failing the whole job because of a bookkeeping write would report a
    completed ingest as failed.
    """
    if subject is None:
        return
    from app.subjects.store import subject_store

    try:
        subject_store.attach_resource(subject.id, resource_id)
    except Exception as exc:  # noqa: BLE001 - attach must not fail an ingest
        log.warning("ingest done but subject attach failed: %s", exc)


@router.post("/courses/{course_id}/ingest")
async def ingest_file(
    course_id: str,
    file: Annotated[UploadFile, File()],
    # Optional, same semantics as the YouTube route's `subject_id`: when
    # present the file is ingested under the subject's real course_id and
    # auto-attached to the subject on success. Omitted -> byte-identical
    # behaviour to before.
    subject_id: Annotated[str | None, Form()] = None,
    # "full" (default) ingests every window in one job; "stream" ingests only
    # the first INITIAL_WINDOWS and leaves the rest to /position. Video files
    # only: documents and images ignore it. YouTube ingest is unchanged.
    mode: Annotated[str, Form()] = "full",
) -> dict:
    if mode not in INGEST_MODES:
        raise HTTPException(
            status_code=422, detail="mode must be 'full' or 'stream'"
        )
    try:
        source_type = source_type_for(
            file.filename or "", content_type=file.content_type
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # The only moment the learner's own filename exists. It is captured here
    # and carried into the resource title, because from the next line on the
    # file is the staged `{resource_id}.{ext}` asset whose name is an internal
    # identifier and not what the learner uploaded. Images go through this
    # exact path, so `diagram.png` gets title == "diagram.png".
    original_name = sanitize_filename(file.filename)

    target_course_id, subject = _resolve_ingest_subject(course_id, subject_id)
    _take_upload_slot()

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    resource_id = _resource_id()
    suffix = upload_suffix_for(
        file.filename, source_type=source_type, content_type=file.content_type
    )
    target = UPLOAD_DIR / f"{resource_id}{suffix}"
    max_bytes = MAX_UPLOAD_BYTES
    if settings.max_upload_mb > 0:
        max_bytes = min(max_bytes, settings.max_upload_mb * 1024 * 1024)
    size = 0
    with target.open("wb") as handle:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > max_bytes:
                handle.close()
                target.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=413,
                    detail=f"upload exceeds {max_bytes // (1024*1024)}MB",
                )
            handle.write(chunk)
    if size == 0:
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail="uploaded file is empty")
    if source_type == "video" and settings.max_video_minutes > 0:
        # Checked from the container header, before any model call is spent.
        duration = media_duration_ms(target)
        if duration is not None and duration > settings.max_video_minutes * 60_000:
            target.unlink(missing_ok=True)
            raise HTTPException(
                status_code=413,
                detail=(
                    f"video is {duration // 60_000} min; this deployment accepts "
                    f"videos up to {settings.max_video_minutes} min"
                ),
            )

    job = new_job(target_course_id, resource_id)
    log.info(
        "queued ingest job=%s course=%s resource=%s type=%s bytes=%d subject=%s",
        job.job_id, target_course_id, resource_id, source_type, size, subject_id,
    )

    if source_type == "video":
        def work() -> dict:
            report = ingest_media_windows(
                target, course_id=target_course_id, resource_id=resource_id,
                title=original_name, mode=mode,
                # Stream: attach as soon as the first window is queryable.
                on_window_done=(
                    (lambda i: _attach_to_subject(subject, resource_id) if i == 0 else None)
                    if mode == "stream"
                    else None
                ),
            )
            _attach_to_subject(subject, resource_id)
            return report
    elif source_type == "image":
        # Ungated like pdf/pptx: described by the video visual pass's vision
        # call, indexed with source_type "image" and an image number. A
        # description that cannot be produced raises, and run_ingest records
        # the job as failed rather than indexing invented text.
        def work() -> dict:
            report = ingest_image(
                target, course_id=target_course_id, resource_id=resource_id,
                title=original_name,
            )
            _attach_to_subject(subject, resource_id)
            return report
    else:
        def work() -> dict:
            report = ingest_document(
                target, course_id=target_course_id, resource_id=resource_id,
                source_type=source_type, title=original_name,
            )
            _attach_to_subject(subject, resource_id)
            return report
    schedule(job, work)
    return job_payload(job)


@router.post("/courses/{course_id}/ingest-youtube")
async def ingest_youtube_route(course_id: str, body: YouTubeIngestRequest) -> dict:
    from app.ingestion.youtube import parse_youtube_id

    if not parse_youtube_id(body.url):
        raise HTTPException(
            status_code=422,
            detail=f"not a recognisable YouTube URL: {body.url!r}",
        )
    resource_id = _resource_id()
    target_course_id = course_id
    subject = None
    if body.subject_id is not None:
        from app.subjects.store import subject_store

        subject = subject_store.get(body.subject_id)
        if subject is None:
            raise HTTPException(
                status_code=404, detail=f"unknown subject {body.subject_id}"
            )
        if not subject.course_id:
            raise HTTPException(
                status_code=422,
                detail=f"subject {body.subject_id} is not linked to a course",
            )
        # The subject owns the real course; the path id is only a route hint.
        target_course_id = subject.course_id

    job = new_job(target_course_id, resource_id)
    log.info(
        "queued youtube ingest job=%s course=%s resource=%s subject=%s",
        job.job_id, target_course_id, resource_id, body.subject_id,
    )

    def _work() -> None:
        ingest_youtube(body.url, course_id=target_course_id, resource_id=resource_id)
        if subject is not None:
            from app.subjects.store import subject_store

            try:
                subject_store.attach_resource(subject.id, resource_id)
            except Exception as exc:  # noqa: BLE001 - attach must not fail an ingest
                log.warning(
                    "youtube ingest done but subject attach failed: %s", exc
                )

    schedule(job, _work)
    return job_payload(job)


@router.get("/ingestion/jobs/{job_id}")
async def read_job(job_id: str) -> dict:
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"unknown job {job_id}")
    return job_payload(job)


@router.get("/courses/{course_id}/resources")
async def list_resources(course_id: str) -> dict:
    from app.retrieval import registry

    try:
        resources = registry.get_shared_store().resources(course_id)
    except Exception as exc:  # noqa: BLE001 - an empty list beats a 500
        log.warning("resource listing failed for %s: %s", course_id, exc)
        resources = []
    # Attach the on-disk asset name where we have one, so the UI's
    # `resourceMediaUrl(resourceId)` request resolves to a real file.
    for entry in resources:
        asset = resolve_asset(entry["id"])
        if asset is not None:
            entry["asset_name"] = asset.name
    return {"resources": resources}


class PositionRequest(BaseModel):
    t_ms: int = Field(ge=0)


#: In-flight guard: ``(resource_id, kind, window_start_ms) -> job_id``. Held
#: only in memory (state is otherwise derived from the store), mutated under
#: ``_POSITION_LOCK`` so rapid /position calls cannot double-queue a window. At
#: most one job per (resource, kind) runs at a time, which also keeps the
#: transcript frontier contiguous.
_POSITION_LOCK = threading.Lock()
_INFLIGHT: dict[tuple[str, str, int], str] = {}

#: Max transcript windows queued ahead of the playhead per call.
MAX_T_WINDOWS_PER_CALL = 2


def reset_inflight() -> None:
    with _POSITION_LOCK:
        _INFLIGHT.clear()


def _inflight_for(resource_id: str, kind: str) -> list[str]:
    return [
        jid for (rid, k, _w), jid in _INFLIGHT.items() if rid == resource_id and k == kind
    ]


@router.post("/courses/{course_id}/resources/{resource_id}/position")
async def report_position(
    course_id: str, resource_id: str, body: PositionRequest
) -> dict:
    """Playhead report: keep the transcript ahead of the learner, visuals caught up.

    Stateless apart from the in-flight guard: both frontiers and the duration
    come from chunk metadata, so this keeps working after a restart. Queues up
    to two stage-T windows while ``frontier < t_ms + LOOKAHEAD_MS`` and a stage-V
    catch-up job while ``visual_through_ms < processed_through_ms``. ``ready``
    only when both frontiers have reached the duration.
    """
    from app.retrieval import registry
    from app.subjects.store import resource_exists

    if not _SAFE_RESOURCE_ID.fullmatch(resource_id) or not resource_exists(
        resource_id, course_id
    ):
        raise HTTPException(
            status_code=404,
            detail=f"unknown resource {resource_id} in course {course_id}",
        )
    store = registry.get_shared_store()
    info = store.processing_frontier(resource_id) or {}
    frontier = info.get("processed_through_ms")
    vthrough = info.get("visual_through_ms")
    duration = info.get("duration_ms")
    out = {
        "processed_through_ms": frontier,
        "visual_through_ms": vthrough,
        "duration_ms": duration,
    }
    if frontier is None or duration is None:
        return {"status": "ready", "job_ids": [], **out}
    t_complete = frontier >= duration
    # Legacy / no-visual resources carry no marker: never visual-pending.
    v_complete = vthrough is None or vthrough >= duration
    if t_complete and v_complete:
        return {"status": "ready", "job_ids": [], **out}

    t_windows: list[int] = []
    if not t_complete:
        w = frontier // WINDOW_MS
        while (
            len(t_windows) < MAX_T_WINDOWS_PER_CALL
            and w * WINDOW_MS < duration
            and w * WINDOW_MS < body.t_ms + LOOKAHEAD_MS
        ):
            t_windows.append(w)
            w += 1
    v_windows: list[int] = []
    if vthrough is not None and vthrough < frontier:
        v_first = vthrough // WINDOW_MS
        v_last = (frontier + WINDOW_MS - 1) // WINDOW_MS  # exclusive
        v_windows = list(range(v_first, v_last))

    from app.ingestion.backoff import active_quota_block

    blocked = active_quota_block()
    if blocked and (t_windows or v_windows):
        # Queueing now would fail at once and spend more requests; say why.
        return {"status": "failed", "job_ids": [], "error": blocked, **out}

    if not t_windows and not v_windows:
        with _POSITION_LOCK:
            ids = _inflight_for(resource_id, "transcript") + _inflight_for(
                resource_id, "visual"
            )
        return {
            "status": "running" if ids else "idle",
            "job_ids": list(dict.fromkeys(ids)),
            **({"job_id": ids[0]} if ids else {}),
            **out,
        }

    from app.ingestion import jobs as jobs_mod

    with _POSITION_LOCK:
        to_queue: list[tuple[str, list[int]]] = []
        if t_windows and not _inflight_for(resource_id, "transcript"):
            to_queue.append(("transcript", t_windows))
        if v_windows and not _inflight_for(resource_id, "visual"):
            to_queue.append(("visual", v_windows))
        queued: list[str] = []
        if to_queue:
            asset = resolve_asset(resource_id)
            if asset is None:
                raise HTTPException(
                    status_code=409,
                    detail=f"cannot process more of {resource_id}: its media file is not on disk",
                )
            title = None
            try:
                for entry in store.resources(course_id):
                    if entry.get("id") == resource_id:
                        title = entry.get("title")
            except Exception:
                log.debug("title lookup failed for %s", resource_id, exc_info=True)
            for kind, windows in to_queue:
                job = new_job(course_id, resource_id)
                keys = [(resource_id, kind, w * WINDOW_MS) for w in windows]
                for k in keys:
                    _INFLIGHT[k] = job.job_id

                def work(kind=kind, windows=windows, keys=keys) -> dict:
                    try:
                        return jobs_mod.ingest_media_windows(
                            asset, course_id=course_id, resource_id=resource_id,
                            title=title, mode="stream", start_window=windows[0],
                            n_windows=len(windows), stages=(kind,),
                        )
                    finally:
                        with _POSITION_LOCK:
                            for k in keys:
                                _INFLIGHT.pop(k, None)

                queued.append(job.job_id)
                schedule(job, work)
        running = _inflight_for(resource_id, "transcript") + _inflight_for(
            resource_id, "visual"
        )
    job_ids = list(dict.fromkeys(queued + running))
    body_out = {
        "status": "queued" if queued else "running",
        "job_ids": job_ids,
        **out,
    }
    if job_ids:
        body_out["job_id"] = job_ids[0]
    return body_out


@router.delete("/courses/{course_id}/resources/{resource_id}")
async def delete_resource(course_id: str, resource_id: str) -> dict:
    """Delete one resource: detach from every subject, then purge it.

    The course in the path must own the resource; otherwise 404 and nothing is
    touched. Subjects have no reverse index, so every subject is scanned.
    Detach runs before purge so no subject is left pointing at a deleted id.
    """
    # Local imports: purge imports resolve_asset from this module.
    from app.ingestion.purge import purge_resource
    from app.subjects.store import resource_exists, subject_store

    if not _SAFE_RESOURCE_ID.fullmatch(resource_id) or not resource_exists(
        resource_id, course_id
    ):
        raise HTTPException(
            status_code=404,
            detail=f"unknown resource {resource_id} in course {course_id}",
        )

    detached_from: list[str] = []
    for subject in subject_store.list_all():
        if resource_id in subject.resource_ids:
            subject_store.detach_resource(subject.id, resource_id)
            detached_from.append(subject.id)

    try:
        report = purge_resource(resource_id)
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"chunks removed but staged asset for {resource_id} could not be deleted",
        ) from exc

    return {
        "resource_id": resource_id,
        "deleted": True,
        "detached_from": detached_from,
        "asset_removed": bool(report["asset_removed"]),
    }


@router.get("/resources/{resource_id}/media")
async def stream_media(resource_id: str) -> FileResponse:
    """Stream a resource's stored asset for the player's per-resource URL."""
    asset = resolve_asset(resource_id)
    if asset is None:
        raise HTTPException(
            status_code=404, detail=f"no stored media for {resource_id}"
        )
    return FileResponse(asset)


@router.get("/resources/{resource_id}/media/{filename}")
async def stream_media_named(resource_id: str, filename: str) -> FileResponse:
    """Same asset, under a URL that ends in a file name.

    External viewers (Office Online for slides) pick the format from the URL's
    extension. ``filename`` is cosmetic: the file is still resolved from the
    resource id alone, through :func:`resolve_asset`.
    """
    return await stream_media(resource_id)


def clear_uploads() -> None:
    """Test helper: remove everything under the upload directory."""
    shutil.rmtree(UPLOAD_DIR, ignore_errors=True)