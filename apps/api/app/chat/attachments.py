"""Images attached to one chat: ``POST /chat/attachments``, ``DELETE /chat/attachments/{id}``.

An attached image is NOT a resource: it is never ingested, indexed or filed
under a subject. It is transcribed once by the vision model (text and math
verbatim, figures described) and that transcript joins the chat's context:
every later answer in the chat sees it through the system prompt
(``prompts.style_note``), on every answer path.

It is learner-provided context, not course evidence: it has no chunk id, so it
can never be cited as lesson material, and the timestamp gate and citation
validation are untouched.

Storage, when Supabase is configured: the transcript in ``chat_attachments``
(migrations 0002 + 0003) and the image file in Storage under
``{owner}/chat/{session}/{id}.ext``. Otherwise process-local (no image file),
mirrored to ``data/chat_attachments.json`` once the lifespan enables persistence.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

from app.chat.sessions import history_key
from app.config import settings
from app.ingestion.backoff import QuotaExhaustedError
from app.supa.client import get_client

log = logging.getLogger(__name__)

router = APIRouter()

TABLE = "chat_attachments"
STATE_PATH = Path("data/chat_attachments.json")
MAX_IMAGE_BYTES = 8 * 1024 * 1024
IMAGE_MIME_TYPES = frozenset(
    {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}
)
MAX_PER_CHAT = 10
#: The newest images a prompt carries, and how much of each.
PROMPT_IMAGES = 5
PROMPT_CHARS_EACH = 4000

_VISION_PROMPT = (
    "Transcribe this image for a tutor who cannot see it. First copy every piece "
    "of text in it exactly, in reading order, writing all math in LaTeX ($...$ "
    "inline, $$...$$ for display equations). Then describe any diagram, graph, "
    "table, chart or figure precisely: labels, axes, values, arrows, what is "
    "connected to what. If it is handwriting, transcribe it as written. Do not "
    "solve, explain or comment on anything; only report what is in the image."
)

_lock = threading.RLock()
#: history key -> attachments, oldest first.
_by_chat: dict[str, list[dict]] = {}
#: Chats whose rows have been read from Supabase into ``_by_chat``.
_loaded: set[str] = set()
_persistence_enabled = False


class AttachmentOut(BaseModel):
    id: str
    name: str
    #: The vision transcript every later answer in the chat is given.
    text: str


def set_persistence_enabled(enabled: bool) -> None:
    global _persistence_enabled
    _persistence_enabled = enabled


def load_local_state() -> None:
    if not STATE_PATH.is_file():
        return
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("ignoring unreadable chat attachments: %s", exc)
        return
    if isinstance(data, dict):
        with _lock:
            _by_chat.clear()
            for key, rows in data.items():
                if isinstance(rows, list):
                    _by_chat[str(key)] = [r for r in rows if isinstance(r, dict)]


def _save_local() -> None:
    if not _persistence_enabled or get_client() is not None:
        return
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _lock:
            body = json.dumps(_by_chat)
        STATE_PATH.write_text(body, encoding="utf-8")
    except OSError as exc:
        log.warning("chat attachments save failed: %s", exc)


def reset() -> None:
    with _lock:
        _by_chat.clear()
        _loaded.clear()


def _rows(session_id: str, thread_id: str) -> list[dict]:
    key = history_key(session_id, thread_id)
    client = get_client()
    if client is not None and key not in _loaded:
        try:
            rows = client.select(
                TABLE,
                {
                    "session_id": f"eq.{session_id}",
                    "thread_id": f"eq.{thread_id}",
                    "order": "created_at.asc",
                },
            )
            with _lock:
                _by_chat[key] = [
                    {
                        "id": r["id"],
                        "path": r.get("storage_path"),
                        "name": r.get("name") or "image",
                        "text": r.get("description") or "",
                    }
                    for r in rows
                    if isinstance(r, dict) and r.get("id")
                ]
                _loaded.add(key)
        except Exception as exc:  # noqa: BLE001 - answer without the images, never fail
            log.warning("chat attachments read failed: %s", exc)
    with _lock:
        return list(_by_chat.get(key, []))


def prompt_note(session_id: str, thread_id: str) -> str:
    """The chat's images as a system-prompt note; empty when it has none."""
    rows = [r for r in _rows(session_id, thread_id) if r.get("text")][-PROMPT_IMAGES:]
    if not rows:
        return ""
    parts = [
        (
            "The learner attached these images to this chat. Each was transcribed by "
            "a vision model; the transcript is exactly what the image shows. Use it "
            "whenever the learner refers to an image, photo, screenshot, figure or "
            "problem in it, or says 'this'. It is the learner's own material, not "
            "course material: never put a [chunk_id=...] marker on anything taken "
            "from it, and say it comes from their image."
        )
    ]
    for i, r in enumerate(rows, 1):
        parts.append(
            f'Image {i} ("{r.get("name") or "image"}"):\n{str(r["text"])[:PROMPT_CHARS_EACH]}'
        )
    return "\n\n".join(parts)


_EXT = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/heic": ".heic",
    "image/heif": ".heif",
}


def storage_path_for(
    owner_id: str | None, session_id: str, attachment_id: str, mime: str
) -> str:
    """``{owner}/chat/{session}/{id}.ext``: the owner's prefix, apart from resources."""
    return (
        f"{owner_id or 'public'}/chat/{session_id}/{attachment_id}{_EXT.get(mime, '')}"
    )


def _describe(data: bytes, mime: str) -> str:
    from google import genai

    if not settings.llm_api_key:
        raise RuntimeError("no vision model configured")
    client = genai.Client(api_key=settings.llm_api_key)
    resp = client.models.generate_content(
        model=settings.llm_model,
        contents=[{"inline_data": {"mime_type": mime, "data": data}}, _VISION_PROMPT],
    )
    return (getattr(resp, "text", "") or "").strip()


def _authed(request: Request, session_id: str):
    from app.chat import router as chat_router

    session = chat_router._authed_session(session_id, request)
    return session


@router.post("/chat/attachments", response_model=AttachmentOut)
async def add_attachment(
    request: Request,
    session_id: Annotated[str, Form()],
    image: Annotated[UploadFile, File()],
    thread_id: Annotated[str | None, Form(max_length=64)] = None,
) -> AttachmentOut:
    from app.chat import router as chat_router

    if not chat_router._check_rate_limit(chat_router._client_ip(request)):
        raise HTTPException(status_code=429, detail="rate limit exceeded")
    session = _authed(request, session_id)
    tid = chat_router._resolve_thread(session.id, thread_id)
    if len(_rows(session.id, tid)) >= MAX_PER_CHAT:
        raise HTTPException(
            status_code=409, detail=f"a chat keeps at most {MAX_PER_CHAT} images"
        )
    mime = (image.content_type or "").split(";")[0].strip().lower()
    if mime not in IMAGE_MIME_TYPES:
        raise HTTPException(
            status_code=422, detail="attach a JPEG, PNG, WebP or HEIC image"
        )
    data = await image.read(MAX_IMAGE_BYTES + 1)
    if not data:
        raise HTTPException(status_code=422, detail="the image is empty")
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"images are limited to {MAX_IMAGE_BYTES // (1024 * 1024)}MB",
        )

    try:
        # Blocking SDK call: off the event loop.
        text = await asyncio.to_thread(_describe, data, mime)
    except QuotaExhaustedError:
        raise
    except Exception as exc:
        log.error("chat image vision error: %s", type(exc).__name__)
        raise HTTPException(
            status_code=502, detail="could not read the image right now"
        ) from exc
    if not text:
        raise HTTPException(
            status_code=422, detail="nothing could be read from the image"
        )

    name = (image.filename or "image").replace("\n", " ")[:120]
    row = {"id": f"att_{uuid.uuid4().hex[:12]}", "name": name, "text": text}
    key = history_key(session.id, tid)
    with _lock:
        _by_chat.setdefault(key, []).append(row)
    if (client := get_client()) is not None:
        # The image itself, in Storage beside the learner's other files; the
        # transcript stays the only thing answers read.
        path = storage_path_for(session.owner_id, session.id, row["id"], mime)
        try:
            await asyncio.to_thread(
                client.upload, settings.supabase_storage_bucket, path, data, mime
            )
        except Exception as exc:  # noqa: BLE001 - the transcript still works
            log.warning("chat image upload failed: %s", exc)
            path = None
        row["path"] = path
        try:
            client.upsert(
                TABLE,
                {
                    "id": row["id"],
                    "owner_id": session.owner_id,
                    "session_id": session.id,
                    "thread_id": tid,
                    "name": name,
                    "mime": mime,
                    "description": text,
                    "storage_path": path,
                    "created_at": datetime.now(UTC).isoformat(),
                },
                on_conflict="id",
            )
        except Exception as exc:  # noqa: BLE001 - kept for this process at least
            log.warning("chat attachment write failed: %s", exc)
    _save_local()
    return AttachmentOut(id=row["id"], name=name, text=text)


@router.delete("/chat/attachments/{attachment_id}", status_code=204)
def delete_attachment(
    attachment_id: str,
    request: Request,
    session_id: str,
    thread_id: str | None = None,
) -> None:
    from app.chat import router as chat_router

    session = _authed(request, session_id)
    tid = chat_router._resolve_thread(session.id, thread_id)
    key = history_key(session.id, tid)
    gone = [r for r in _rows(session.id, tid) if r["id"] == attachment_id]
    with _lock:
        _by_chat[key] = [r for r in _by_chat.get(key, []) if r["id"] != attachment_id]
    if (client := get_client()) is not None:
        paths = [r["path"] for r in gone if r.get("path")]
        try:
            client.remove(settings.supabase_storage_bucket, paths)
        except Exception as exc:  # noqa: BLE001
            log.warning("chat image remove failed: %s", exc)
        try:
            client.delete(
                TABLE, {"id": f"eq.{attachment_id}", "session_id": f"eq.{session.id}"}
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("chat attachment delete failed: %s", exc)
    _save_local()
