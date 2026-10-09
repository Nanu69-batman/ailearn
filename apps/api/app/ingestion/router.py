"""Ingestion routes. Response shapes are frozen by ``apps/web/lib/api.ts``.

    POST /courses/{course_id}/ingest          multipart file upload
    POST /courses/{course_id}/ingest-youtube  {"url": "..."}
    GET  /ingestion/jobs/{job_id}
    GET  /courses/{course_id}/resources
    GET  /resources/{resource_id}/media       (streams a downloaded asset)

``apps/web`` is never modified by the API; these shapes exist to match it.
"""

from __future__ import annotations

import logging
import shutil
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.config import settings
from app.ingestion.jobs import (
    MAX_UPLOAD_BYTES,
    get_job,
    ingest_document,
    ingest_media,
    ingest_youtube,
    job_payload,
    new_job,
    schedule,
    source_type_for,
)

log = logging.getLogger(__name__)

router = APIRouter()

#: Uploads land here and are kept: re-ingest and /media streaming both need
#: the original bytes, and Chroma stores only the derived text.
UPLOAD_DIR = Path(settings.yt_data_dir).parent / "uploads"


class YouTubeIngestRequest(BaseModel):
    url: str = Field(min_length=1)
    #: Optional subject scoping. When present, the resource is ingested under
    #: that subject's real ``course_id`` (derived here, not taken from the
    #: path) and auto-attached to the subject, so it shows up in
    #: ``GET /subjects/{id}/resources`` immediately.
    subject_id: str | None = None


def _resource_id() -> str:
    return f"res_{uuid.uuid4().hex[:12]}"


@router.post("/courses/{course_id}/ingest")
async def ingest_file(
    course_id: str, file: Annotated[UploadFile, File()]
) -> dict:
    try:
        source_type = source_type_for(file.filename or "")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    resource_id = _resource_id()
    target = UPLOAD_DIR / f"{resource_id}{Path(file.filename or '').suffix.lower()}"
    size = 0
    with target.open("wb") as handle:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                handle.close()
                target.unlink(missing_ok=True)
                raise HTTPException(
                    status_code=413,
                    detail=f"upload exceeds {MAX_UPLOAD_BYTES // (1024*1024)}MB",
                )
            handle.write(chunk)
    if size == 0:
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail="uploaded file is empty")

    job = new_job(course_id, resource_id)
    log.info(
        "queued ingest job=%s course=%s resource=%s type=%s bytes=%d",
        job.job_id, course_id, resource_id, source_type, size,
    )

    if source_type == "video":
        work = lambda: ingest_media(
            target, course_id=course_id, resource_id=resource_id
        )
    else:
        work = lambda: ingest_document(
            target, course_id=course_id, resource_id=resource_id,
            source_type=source_type,
        )
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
        match = sorted(UPLOAD_DIR.glob(f"{entry['id']}.*"))
        if match:
            entry["asset_name"] = match[0].name
    return {"resources": resources}


@router.get("/resources/{resource_id}/media")
async def stream_media(resource_id: str) -> FileResponse:
    """Stream a resource's stored asset for the player's per-resource URL."""
    matches = sorted(UPLOAD_DIR.glob(f"{resource_id}.*"))
    if not matches:
        raise HTTPException(
            status_code=404, detail=f"no stored media for {resource_id}"
        )
    return FileResponse(matches[0])


def clear_uploads() -> None:
    """Test helper: remove everything under the upload directory."""
    shutil.rmtree(UPLOAD_DIR, ignore_errors=True)