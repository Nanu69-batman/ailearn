"""Ingestion HTTP API: file + YouTube ingest, jobs, resources.

In-memory job/resource registries keep this dependency-free for tests/demo;
swap the ``_jobs``/``_resources`` dicts for Postgres tables (see
migrations/0001_initial.sql ``ingestion_jobs``/``resources``) without
changing the endpoint shapes.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from app.ingestion.embedder import get_embedder
from app.ingestion.jobs import _chunk_segments
from app.ingestion.transcribe import (
    media_mime_type,
    probe_media_duration_s,
    transcribe_with_gemini,
)
from app.ingestion.youtube import (
    DurationExceededError,
    NormalizedMedia,
    extract_video_id,
    fetch_metadata,
    ingest_youtube,
)

log = logging.getLogger(__name__)

router = APIRouter()

# In-memory registries (Postgres swap documented above).
_courses: dict[str, dict] = {}
_resources: dict[str, dict] = {}
_resources_by_course: dict[str, list[str]] = {}
_jobs: dict[str, dict] = {}

# Monkeypatch seam for tests: override the allowed ingest base directory.
TEST_DATA_DIR: Path | None = None

_VIDEO_SUFFIXES = {".mp4", ".mov", ".webm", ".mkv"}


class IngestBody(BaseModel):
    path: str


class IngestYoutubeBody(BaseModel):
    url: str


def reset_ingestion_state() -> None:
    """Clear courses/resources/jobs (tests/dev only)."""
    _courses.clear()
    _resources.clear()
    _resources_by_course.clear()
    _jobs.clear()


INGEST_STATE_PATH = Path("data/ingest_state.json")


def save_ingest_state() -> None:
    """Persist course/resource/job registries for localhost restarts."""
    import json

    try:
        INGEST_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        INGEST_STATE_PATH.write_text(
            json.dumps(
                {
                    "courses": _courses,
                    "resources": _resources,
                    "resources_by_course": _resources_by_course,
                    "jobs": _jobs,
                }
            ),
            encoding="utf-8",
        )
    except OSError as exc:
        log.warning("ingest state save failed: %s", exc)


def _match_existing_asset(rtype: str, base: Path) -> str | None:
    """Find an on-disk asset of this type to re-attach after a snapshot rebuild.

    Deterministic (sorted) so repeated restarts resolve the same file, and
    type-scoped so a PDF never gets attached to a video resource.
    """
    suffixes = {
        "video": _VIDEO_SUFFIXES,
        "pdf": {".pdf"},
        "pptx": {".pptx", ".ppt"},
        "note": {".txt", ".md"},
    }.get(rtype, set())
    if not suffixes or not base.is_dir():
        return None
    matches = sorted(
        p.name for p in base.iterdir() if p.is_file() and p.suffix.lower() in suffixes
    )
    return matches[0] if matches else None


def ensure_resources_for_chunks(chunks) -> int:
    """Recreate resource registry entries from indexed chunks.

    Fallback for snapshots written before ingest-state persistence existed:
    derives one ``done`` resource per resource_id so the course UI and chat
    scopes keep working after a restart.

    Recovery: when the matching file still exists under test-data/ (matched by
    type), re-attach it so playback works again. Otherwise ``asset_name`` stays
    None and the media endpoint 404s instead of serving the wrong video.
    """
    made = 0
    base = _base_dir()
    for chunk in chunks:
        rid = getattr(chunk, "resource_id", None)
        if not rid or rid in _resources:
            continue
        stype = getattr(chunk, "source_type", "note") or "note"
        rtype = stype if stype in ("video", "pdf", "pptx", "note", "code") else "note"
        course_id = getattr(chunk, "course_id", "demo") or "demo"
        _ensure_course(course_id)
        asset_name = _match_existing_asset(rtype, base)
        _resources[rid] = {
            "id": rid,
            "course_id": course_id,
            "type": rtype,
            "provider": (
                "youtube"
                if (rtype == "video" and getattr(chunk, "video_id", None))
                else "file"
            ),
            "youtube_id": getattr(chunk, "video_id", None),
            # Prefer the real filename; fall back to the id when unknown.
            "title": asset_name or rid,
            "status": "done",
            "asset_name": asset_name,
        }
        _resources_by_course.setdefault(course_id, []).append(rid)
        made += 1
    if made:
        log.info("rebuilt %d resource entries from indexed chunks", made)
    return made


def load_ingest_state() -> int:
    """Reload registries saved by save_ingest_state; returns resource count."""
    import json

    if not INGEST_STATE_PATH.is_file():
        return 0
    try:
        data = json.loads(INGEST_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("ignoring unreadable ingest state: %s", exc)
        return 0
    _courses.update(data.get("courses", {}))
    _resources.update(data.get("resources", {}))
    _resources_by_course.update(data.get("resources_by_course", {}))
    _jobs.update(data.get("jobs", {}))
    log.info("restored %d resources from local ingest state", len(_resources))
    return len(_resources)


def _ensure_course(course_id: str) -> dict:
    if course_id not in _courses:
        _courses[course_id] = {"id": course_id, "title": course_id}
    return _courses[course_id]


def _base_dir() -> Path:
    if TEST_DATA_DIR is not None:
        return TEST_DATA_DIR
    return Path(__file__).resolve().parents[4] / "test-data"


def _resolve_asset(rel_path: str) -> Path:
    """Constrain ingest paths to the test-data dir; traversal -> 422."""
    if not rel_path or not rel_path.strip():
        raise HTTPException(status_code=422, detail="path is required")
    candidate = Path(rel_path.strip())
    if candidate.is_absolute():
        raise HTTPException(status_code=422, detail="absolute paths are not allowed")
    base = _base_dir().resolve()
    full = (base / candidate).resolve()
    if full != base and base not in full.parents:
        raise HTTPException(status_code=422, detail="path escapes test-data dir")
    if not full.is_file():
        raise HTTPException(status_code=404, detail="file not found in test-data")
    return full


def _resource_type_for(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in _VIDEO_SUFFIXES:
        return "video"
    if suffix == ".pdf":
        return "pdf"
    if suffix in (".pptx", ".ppt"):
        return "pptx"
    if suffix in (".py", ".js", ".ts", ".tsx", ".java", ".go", ".rs"):
        return "code"
    return "note"


def _new_ids() -> tuple[str, str]:
    return uuid.uuid4().hex[:12], uuid.uuid4().hex[:12]


def _finish_job_files(
    job_id: str,
    course_id: str,
    resource_id: str,
    asset: Path,
    rtype: str,
    title: str,
) -> None:
    """Background worker: extract -> chunk -> embed -> shared-store add."""
    from app.ingestion.extractors import extract
    from app.retrieval.registry import get_shared_store, save_snapshot

    _jobs[job_id]["status"] = "processing"
    try:
        if rtype == "video":
            # The duration must be measured BEFORE transcription. Without it
            # every duration guard in transcribe.py is skipped, and a drifted
            # transcript (Gemini claiming ~1.5x the real length) gets indexed,
            # which breaks the strict-T no-spoiler gate. This call site is the
            # exact regression that let those guards sleep.
            duration_s = probe_media_duration_s(asset)
            if duration_s is None:
                log.warning(
                    "could not measure duration of %s; transcript timestamps "
                    "for this resource will not be checked against the media",
                    asset.name,
                )
            segments = transcribe_with_gemini(str(asset), title, duration_s=duration_s)
        else:
            segments = extract(asset, rtype)
        row = {"id": resource_id, "course_id": course_id, "type": rtype}
        embedder = get_embedder()
        chunks = _chunk_segments(segments, row, embedder.name)
        vectors = embedder.embed_batch([c.text for c in chunks]) if chunks else []
        for chunk, vec in zip(chunks, vectors):
            chunk.embedding = vec
        store = get_shared_store()
        # Re-ingestion reuses the same resource_id: drop stale chunks so the
        # store never accumulates duplicates for one resource.
        store.remove_resource(resource_id)
        store.index(chunks)
        save_snapshot()
        save_ingest_state()
        _resources[resource_id]["status"] = "done"
        _jobs[job_id]["status"] = "done"
        log.info("ingest job %s done (%d chunks)", job_id, len(chunks))
    except Exception as exc:  # noqa: BLE001 - job records the failure
        _resources[resource_id]["status"] = "failed"
        _jobs[job_id]["status"] = "failed"
        _jobs[job_id]["error"] = str(exc)
        log.warning("ingest job %s failed: %s", job_id, type(exc).__name__)


def _finish_job_youtube(
    job_id: str, course_id: str, resource_id: str, url: str
) -> None:
    """Background worker for YouTube: pipeline -> chunk -> embed -> store."""
    from app.retrieval.registry import get_shared_store, save_snapshot

    _jobs[job_id]["status"] = "processing"
    try:
        media: NormalizedMedia = ingest_youtube(url)
        row = {
            "id": resource_id,
            "course_id": course_id,
            "type": "video",
            "video_id": media.video_id,
        }
        embedder = get_embedder()
        chunks = _chunk_segments(media.segments, row, embedder.name)
        vectors = embedder.embed_batch([c.text for c in chunks]) if chunks else []
        for chunk, vec in zip(chunks, vectors):
            chunk.embedding = vec
        store = get_shared_store()
        store.remove_resource(resource_id)
        store.index(chunks)
        save_snapshot()
        save_ingest_state()
        _resources[resource_id].update(
            {"status": "done", "title": media.title, "youtube_id": media.video_id}
        )
        _jobs[job_id]["status"] = "done"
        log.info("youtube job %s done (%d chunks)", job_id, len(chunks))
    except Exception as exc:  # noqa: BLE001 - job records the failure
        _resources[resource_id]["status"] = "failed"
        _jobs[job_id]["status"] = "failed"
        _jobs[job_id]["error"] = str(exc)
        log.warning("youtube job %s failed: %s", job_id, type(exc).__name__)


@router.post("/courses/{course_id}/ingest", status_code=202)
def ingest_file(course_id: str, body: IngestBody, background: BackgroundTasks) -> dict:
    """Ingest a test-data file (mp4 -> transcribe; pdf/pptx/txt -> extractors)."""
    asset = _resolve_asset(body.path)
    _ensure_course(course_id)
    rtype = _resource_type_for(asset)
    # Idempotency: re-ingesting the same asset under a course reuses the
    # existing resource instead of creating a duplicate row.
    #
    # Status is deliberately NOT part of the match. A job that fails leaves the
    # row at ``failed`` while its previous chunks stay indexed; if ``failed``
    # were excluded, the *next* re-ingest of the same file would mint a fresh
    # resource_id and leave the old resource permanently stranded in scope,
    # polluting retrieval with stale text. Match on identity only.
    resource_id = None
    for rid in _resources_by_course.get(course_id, []):
        existing = _resources.get(rid)
        if (
            existing is not None
            and existing.get("asset_name") == asset.name
            and existing.get("type") == rtype
        ):
            resource_id = rid
            break
    if resource_id is None:
        resource_id, _ = _new_ids()
        _resources[resource_id] = {
            "id": resource_id,
            "course_id": course_id,
            "type": rtype,
            "provider": "file",
            "youtube_id": None,
            "title": asset.name,
            "status": "pending",
            # Relative name so the media endpoint can stream this exact asset.
            "asset_name": asset.name,
        }
        _resources_by_course.setdefault(course_id, []).append(resource_id)
    else:
        _resources[resource_id]["status"] = "pending"
        _resources[resource_id].pop("error", None)
    _, job_id = _new_ids()
    _jobs[job_id] = {
        "job_id": job_id,
        "course_id": course_id,
        "resource_id": resource_id,
        "status": "pending",
        "error": None,
    }
    background.add_task(
        _finish_job_files, job_id, course_id, resource_id, asset, rtype, asset.name
    )
    return {"job_id": job_id, "resource_id": resource_id, "status": "pending"}


@router.post("/courses/{course_id}/ingest-youtube", status_code=202)
def ingest_youtube_url(
    course_id: str, body: IngestYoutubeBody, background: BackgroundTasks
) -> dict:
    """Ingest a YouTube URL (captions preferred, else download + transcribe)."""
    from app.config import settings

    url = (body.url or "").strip()
    try:
        extract_video_id(url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        meta = fetch_metadata(url)
    except (ValueError, DurationExceededError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail="upstream provider error") from exc
    except Exception as exc:
        # Metadata fetch failed (network/parse); report as upstream failure.
        raise HTTPException(status_code=502, detail="upstream provider error") from exc
    try:
        if (
            meta.duration_s is not None
            and meta.duration_s > settings.yt_max_minutes * 60
        ):
            raise DurationExceededError(
                f"video is {meta.duration_s / 60:.1f} min; "
                f"limit is {settings.yt_max_minutes} min"
            )
    except DurationExceededError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _ensure_course(course_id)
    resource_id, job_id = _new_ids()
    _resources[resource_id] = {
        "id": resource_id,
        "course_id": course_id,
        "type": "video",
        "provider": "youtube",
        "youtube_id": meta.video_id,
        "title": meta.title,
        "status": "pending",
    }
    _resources_by_course.setdefault(course_id, []).append(resource_id)
    _jobs[job_id] = {
        "job_id": job_id,
        "course_id": course_id,
        "resource_id": resource_id,
        "status": "pending",
        "error": None,
    }
    background.add_task(_finish_job_youtube, job_id, course_id, resource_id, url)
    return {"job_id": job_id, "resource_id": resource_id, "status": "pending"}


@router.get("/ingestion/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return dict(job)


@router.get("/courses/{course_id}/resources")
def list_resources(course_id: str) -> dict:
    _ensure_course(course_id)
    items = [_resources[rid] for rid in _resources_by_course.get(course_id, [])]
    return {"course_id": course_id, "resources": [dict(r) for r in items]}


@router.get("/resources/{resource_id}/media")
def get_resource_media(resource_id: str):
    """Stream an ingested file asset so the player shows THIS resource.

    Each file resource gets its own URL; the frontend must never fall back to
    a single shared demo video. Path is re-validated against the test-data dir.
    """
    from fastapi.responses import FileResponse

    resource = _resources.get(resource_id)
    if resource is None:
        raise HTTPException(status_code=404, detail="resource not found")
    asset_name = resource.get("asset_name")
    if not asset_name:
        raise HTTPException(status_code=404, detail="no file asset for resource")
    try:
        asset = _resolve_asset(asset_name)
    except HTTPException as exc:
        raise HTTPException(status_code=404, detail="asset missing on disk") from exc
    media_type = media_mime_type(asset)
    return FileResponse(
        path=asset,
        media_type=media_type,
        filename=resource.get("title") or asset.name,
    )
