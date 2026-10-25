"""Mirror staged uploads to Supabase Storage + the ``resources`` table.

Render's disk is ephemeral, so the local ``{resource_id}.{suffix}`` file (still
what ingestion and ``resolve_asset`` use) is copied to Storage in the
background and recorded in ``resources``. If the local file later goes missing,
:func:`restore_asset` downloads it back to the exact path ``resolve_asset``
expects. Every function here is best-effort: with Supabase off it is a no-op,
and a Supabase failure logs a warning and never fails the caller.
"""

from __future__ import annotations

import logging
import mimetypes
import os
import threading
from pathlib import Path

from app.config import settings
from app.supa.client import get_client

log = logging.getLogger(__name__)

_threads: list[threading.Thread] = []
_threads_lock = threading.Lock()


def storage_path_for(owner_id: str | None, resource_id: str, filename: str) -> str:
    return f"{owner_id or 'public'}/{resource_id}/{filename}"


def _spawn(fn, *args) -> None:
    thread = threading.Thread(target=fn, args=args, daemon=True)
    with _threads_lock:
        _threads[:] = [t for t in _threads if t.is_alive()]
        _threads.append(thread)
    thread.start()


def wait_idle(timeout: float = 10.0) -> None:
    """Test helper: join in-flight background mirrors."""
    with _threads_lock:
        pending = list(_threads)
    for thread in pending:
        thread.join(timeout)


def _upsert_row(client, row: dict) -> None:
    client.upsert("resources", row, on_conflict="id")


def _mirror_file(
    path: Path, row: dict, owner_id: str | None, content_type: str
) -> None:
    client = get_client()
    if client is None:
        return
    row = dict(row)
    try:
        data = path.read_bytes()
        key = storage_path_for(owner_id, row["id"], path.name)
        client.upload(settings.supabase_storage_bucket, key, data, content_type)
        row["storage_path"] = key
    except Exception as exc:  # noqa: BLE001 - mirroring must never fail an ingest
        log.warning("storage upload failed for %s: %s", row["id"], exc)
    try:
        _upsert_row(client, row)
    except Exception as exc:  # noqa: BLE001
        log.warning("resources upsert failed for %s: %s", row["id"], exc)


def mirror_upload(
    path: Path,
    *,
    resource_id: str,
    owner_id: str | None,
    course_id: str,
    subject_id: str | None,
    title: str | None,
    source_type: str,
    mime: str | None,
    size_bytes: int,
) -> None:
    """Upload ``path`` and upsert its row, off the request path."""
    if get_client() is None:
        return
    content_type = mime or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    row = {
        "id": resource_id,
        "owner_id": owner_id,
        "course_id": course_id,
        "subject_id": subject_id,
        "title": title,
        "source_type": source_type,
        "storage_path": None,
        "mime": content_type,
        "size_bytes": size_bytes,
        "status": "uploaded",
    }
    _spawn(_mirror_file, path, row, owner_id, content_type)


def record_youtube(
    *,
    resource_id: str,
    owner_id: str | None,
    course_id: str,
    subject_id: str | None,
    title: str | None = None,
) -> None:
    if get_client() is None:
        return
    row = {
        "id": resource_id,
        "owner_id": owner_id,
        "course_id": course_id,
        "subject_id": subject_id,
        "title": title,
        "source_type": "youtube",
        "storage_path": None,
        "status": "queued",
    }

    def _go() -> None:
        client = get_client()
        if client is None:
            return
        try:
            _upsert_row(client, row)
        except Exception as exc:  # noqa: BLE001
            log.warning("resources upsert failed for %s: %s", resource_id, exc)

    _spawn(_go)


def get_row(resource_id: str) -> dict | None:
    """The ``resources`` row, or ``None`` (off, absent, or Supabase failing)."""
    client = get_client()
    if client is None:
        return None
    try:
        rows = client.select("resources", {"id": f"eq.{resource_id}"})
    except Exception as exc:  # noqa: BLE001
        log.warning("resources lookup failed for %s: %s", resource_id, exc)
        return None
    return rows[0] if rows else None


def may_delete(resource_id: str, user_id: str | None) -> bool:
    """A row with an owner may be deleted only by that owner."""
    row = get_row(resource_id)
    owner = row.get("owner_id") if row else None
    return owner is None or owner == user_id


def restore_asset(resource_id: str, upload_dir: Path) -> Path | None:
    """Download a missing local asset back to ``{upload_dir}/{resource_id}{suffix}``."""
    row = get_row(resource_id)
    key = row.get("storage_path") if row else None
    if not key:
        return None
    suffix = Path(key).suffix
    if not suffix or "/" in suffix or "\\" in suffix:
        return None
    client = get_client()
    if client is None:
        return None
    target = Path(upload_dir) / f"{resource_id}{suffix}"
    try:
        data = client.download(settings.supabase_storage_bucket, key)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{target.name}.part")
        tmp.write_bytes(data)
        os.replace(tmp, target)
    except Exception as exc:  # noqa: BLE001
        log.warning("storage restore failed for %s: %s", resource_id, exc)
        return None
    return target


def remove_remote(resource_id: str) -> None:
    """Best-effort: drop the Storage object and the ``resources`` row."""
    client = get_client()
    if client is None:
        return
    row = get_row(resource_id)
    key = row.get("storage_path") if row else None
    if key:
        try:
            client.remove(settings.supabase_storage_bucket, [key])
        except Exception as exc:  # noqa: BLE001
            log.warning("storage remove failed for %s: %s", resource_id, exc)
    try:
        client.delete("resources", {"id": f"eq.{resource_id}"})
    except Exception as exc:  # noqa: BLE001
        log.warning("resources delete failed for %s: %s", resource_id, exc)
