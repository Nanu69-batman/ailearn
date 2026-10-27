"""The Malai chat list, per signed-in user: ``GET/PUT/DELETE /malai/chats``.

The sidebar used to keep this index in the browser only, so it was lost with
the browser's storage and never followed the learner to another device. Each
row names a chat (title, subject, preview) and points at the session + thread
its messages live in; the messages themselves are in ``chat_messages``.

Storage: Supabase ``malai_chats`` (supabase/migrations/0002_malai_chats.sql)
when Supabase is configured, else a process-local dict mirrored to
``data/malai_chats.json`` once the app lifespan enables persistence.

The session token is never stored: it is minted on read, and only for a
session the caller owns, so a row cannot unlock someone else's chat.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.auth import AuthUser, get_current_user
from app.chat.launch import create_session_token
from app.chat.sessions import session_store, valid_thread_id
from app.config import settings
from app.supa.client import get_client

log = logging.getLogger(__name__)

router = APIRouter()

TABLE = "malai_chats"
STATE_PATH = Path("data/malai_chats.json")
MAX_CHATS_PER_USER = 500

_lock = threading.RLock()
_local: dict[str, dict[str, dict]] = {}
_persistence_enabled = False


class MalaiChatIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    session_id: str = Field(min_length=1, max_length=128)
    thread_id: str = Field(min_length=1, max_length=64)
    title: str = Field(default="", max_length=200)
    subject_id: str | None = Field(default=None, max_length=128)
    resource_id: str | None = Field(default=None, max_length=128)
    preview: str = Field(default="", max_length=300)
    created_ms: int = Field(ge=0)
    updated_ms: int = Field(ge=0)


class MalaiChatOut(MalaiChatIn):
    id: str
    #: Minted for the caller's own live session; empty when the session is gone.
    session_token: str = ""


class MalaiChatList(BaseModel):
    chats: list[MalaiChatOut]


def set_persistence_enabled(enabled: bool) -> None:
    global _persistence_enabled
    _persistence_enabled = enabled


def load_local_state() -> None:
    if not STATE_PATH.is_file():
        return
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("ignoring unreadable chat list: %s", exc)
        return
    if isinstance(data, dict):
        with _lock:
            _local.clear()
            for owner, rows in data.items():
                if isinstance(rows, dict):
                    _local[str(owner)] = {str(k): v for k, v in rows.items() if isinstance(v, dict)}


def _save_local() -> None:
    if not _persistence_enabled:
        return
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _lock:
            body = json.dumps(_local)
        STATE_PATH.write_text(body, encoding="utf-8")
    except OSError as exc:
        log.warning("chat list save failed: %s", exc)


def reset_local() -> None:
    with _lock:
        _local.clear()


def _user(user: Annotated[AuthUser | None, Depends(get_current_user)]) -> AuthUser:
    # The list is personal: without a login the browser keeps its own copy.
    if user is None:
        raise HTTPException(status_code=401, detail="authentication required")
    return user


def _valid_id(chat_id: str) -> str:
    if not chat_id or len(chat_id) > 64 or not all(c.isalnum() or c in "-_" for c in chat_id):
        raise HTTPException(status_code=422, detail="invalid chat id")
    return chat_id


def _rows(owner: str) -> list[dict]:
    client = get_client()
    if client is None:
        with _lock:
            return [dict(r) for r in _local.get(owner, {}).values()]
    try:
        return client.select(
            TABLE,
            {"owner_id": f"eq.{owner}", "order": "updated_ms.desc", "limit": str(MAX_CHATS_PER_USER)},
        )
    except Exception as exc:
        log.warning("chat list read failed: %s", exc)
        raise HTTPException(status_code=503, detail="chat list unavailable") from exc


def _out(row: dict, owner: str) -> MalaiChatOut:
    session = session_store.get(str(row.get("session_id")))
    token = ""
    if session is not None and session.owner_id == owner:
        token = create_session_token(settings.lms_launch_secret, session.id, session.learner_id)
    return MalaiChatOut(
        id=str(row["id"]),
        session_id=str(row.get("session_id") or ""),
        thread_id=str(row.get("thread_id") or ""),
        title=str(row.get("title") or ""),
        subject_id=row.get("subject_id"),
        resource_id=row.get("resource_id"),
        preview=str(row.get("preview") or ""),
        created_ms=int(row.get("created_ms") or 0),
        updated_ms=int(row.get("updated_ms") or 0),
        session_token=token,
    )


@router.get("/malai/chats", response_model=MalaiChatList)
def list_chats(user: Annotated[AuthUser, Depends(_user)]) -> MalaiChatList:
    rows = sorted(_rows(user.id), key=lambda r: int(r.get("updated_ms") or 0), reverse=True)
    return MalaiChatList(chats=[_out(r, user.id) for r in rows[:MAX_CHATS_PER_USER]])


@router.put("/malai/chats/{chat_id}", response_model=MalaiChatOut)
def put_chat(
    chat_id: str, body: MalaiChatIn, user: Annotated[AuthUser, Depends(_user)]
) -> MalaiChatOut:
    _valid_id(chat_id)
    if not valid_thread_id(body.thread_id):
        raise HTTPException(status_code=422, detail="invalid thread id")
    # Only a session the caller owns can be filed in their list.
    session = session_store.get(body.session_id)
    if session is None or session.owner_id != user.id:
        raise HTTPException(status_code=404, detail="session not found")
    row = {"id": chat_id, "owner_id": user.id, **body.model_dump()}
    client = get_client()
    if client is None:
        with _lock:
            mine = _local.setdefault(user.id, {})
            if chat_id not in mine and len(mine) >= MAX_CHATS_PER_USER:
                raise HTTPException(status_code=409, detail="too many chats; delete some first")
            mine[chat_id] = row
        _save_local()
    else:
        try:
            client.upsert(TABLE, row, on_conflict="owner_id,id")
        except Exception as exc:
            log.warning("chat list write failed: %s", exc)
            raise HTTPException(status_code=503, detail="chat list unavailable") from exc
    return _out(row, user.id)


@router.delete("/malai/chats/{chat_id}", status_code=204)
def delete_chat(chat_id: str, user: Annotated[AuthUser, Depends(_user)]) -> None:
    _valid_id(chat_id)
    client = get_client()
    if client is None:
        with _lock:
            _local.get(user.id, {}).pop(chat_id, None)
        _save_local()
        return
    try:
        client.delete(TABLE, {"id": f"eq.{chat_id}", "owner_id": f"eq.{user.id}"})
    except Exception as exc:
        log.warning("chat list delete failed: %s", exc)
        raise HTTPException(status_code=503, detail="chat list unavailable") from exc
