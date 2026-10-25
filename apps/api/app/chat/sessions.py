"""Session/message stores (in-memory, JSON-persisted; Postgres in production).

The API layer stays stateless: these singletons are process-local for the
prototype/tests. Production swaps them for SQLAlchemy repositories behind the
same method names (see ``SessionRepository`` / ``MessageRepository``).

Backend selection:
  USE_IN_MEMORY=true  (default) -> process-local stores, no DB required.
  USE_IN_MEMORY=false + a real reachable DATABASE_URL -> Postgres-backed
  repos exposing the same methods. Tests always run with USE_IN_MEMORY=true
  so nothing requires network/DB access.

Persistence:
  In-memory stores are mirrored to ``data/chat_sessions.json`` using the same
  save/load idiom as ``app.ingestion.router`` (``save_ingest_state`` /
  ``load_ingest_state``), so a session survives an API restart instead of
  turning every open browser tab into a 404. Persistence is OFF until the app
  lifespan enables it (see ``set_persistence_enabled``), which keeps the test
  suite from writing to the developer's real ``data/`` directory.
  Only session/message rows are persisted — never anything from ``.env``.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import uuid
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from app.supa.client import get_client

log = logging.getLogger(__name__)

# Guards the module-level singletons below.
#
# Why: FastAPI serves each request on a worker thread, and every mutating
# store method mirrors itself to disk synchronously (``save_session_state``).
# ``snapshot()`` therefore walks ``_sessions`` / ``_messages`` -- real dicts --
# from one thread while another thread's ``create()`` / ``append()`` inserts
# into them. On CPython that raises ``RuntimeError: dictionary changed size
# during iteration`` inside the request that happened to be serialising, which
# is a 500 for an unrelated in-flight request. A lock around the dict
# operations plus a copy-before-serialise removes the interleaving entirely.
_state_lock = threading.RLock()

# Sessions older than this are dropped when the state file is loaded, so a
# long-lived dev box does not resurrect months-old sessions forever.
SESSION_TTL = timedelta(days=7)

# Same seam style as ingestion's INGEST_STATE_PATH: tests monkeypatch this to a
# tmp_path. Relative so it resolves against apps/api/ (the working dir when the
# API runs), matching data/ingest_state.json.
SESSION_STATE_PATH = Path("data/chat_sessions.json")

_persistence_enabled = False


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat()


def set_persistence_enabled(enabled: bool) -> None:
    """Enable/disable mirroring the stores to ``SESSION_STATE_PATH``.

    Called by the app lifespan. Tests leave it off (or monkeypatch it on
    against a tmp path) so no test run mutates real state.
    """
    global _persistence_enabled
    _persistence_enabled = enabled


def persistence_enabled() -> bool:
    return _persistence_enabled


@dataclass
class Session:
    id: str
    learner_id: str
    course_id: str
    resource_id: str
    lesson_id: str | None
    resource_scope: list[str]
    current_time_ms: int
    created_at: str = field(default_factory=_utcnow_iso)
    #: Authenticated Supabase user id that opened the session; None = anonymous.
    owner_id: str | None = None


@dataclass
class StoredMessage:
    id: str
    session_id: str
    role: str  # "user" | "assistant"
    content: str
    citations: list[dict] = field(default_factory=list)
    created_at: str = field(default_factory=_utcnow_iso)


# ---------------------------------------------------------------------------
# Chats (threads) within a session.
#
# A learner can keep several chats. Each is a separate message history inside
# the same launch session, so the session's scope, grant and token are shared
# and nothing about the safety checks changes: the chat service never sees a
# thread at all. Which history it reads and writes is chosen by the
# ``current_thread`` context variable, set per request by the router, and the
# message store keys that thread's rows apart. A ContextVar is per request
# (per asyncio task / worker thread), so concurrent chats cannot cross.
# ---------------------------------------------------------------------------

#: The chat every session has from the start; its rows keep the bare session
#: id as their key, so histories written before threads existed are this one.
MAIN_THREAD = "main"
MAX_THREADS_PER_SESSION = 50
THREAD_TITLE_MAX = 80
_THREAD_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")

current_thread: ContextVar[str] = ContextVar("current_thread", default=MAIN_THREAD)


def valid_thread_id(thread_id: str) -> bool:
    return bool(_THREAD_ID.fullmatch(thread_id or ""))


def history_key(session_id: str, thread_id: str | None = None) -> str:
    """Message-store key for one chat of a session."""
    tid = thread_id if thread_id is not None else current_thread.get()
    return session_id if tid == MAIN_THREAD else f"{session_id}#{tid}"


@dataclass
class ChatThread:
    id: str
    session_id: str
    #: None until the learner renames it; the list then shows the first
    #: question asked in it instead.
    title: str | None = None
    created_at: str = field(default_factory=_utcnow_iso)


class InMemoryThreadStore:
    def __init__(self) -> None:
        self._threads: dict[str, dict[str, ChatThread]] = {}

    def snapshot(self) -> dict[str, list[dict]]:
        with _state_lock:
            return {
                sid: [asdict(t) for t in list(ts.values())]
                for sid, ts in list(self._threads.items())
                if ts
            }

    def restore(self, data: dict[str, list[dict]]) -> None:
        for sid, rows in list(data.items()):
            if not isinstance(rows, list):
                continue
            for row in rows:
                try:
                    t = ChatThread(
                        id=str(row["id"]),
                        session_id=str(row["session_id"]),
                        title=row.get("title"),
                        created_at=str(row.get("created_at") or _utcnow_iso()),
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    log.warning("skipping unreadable thread row in %s: %s", sid, exc)
                    continue
                if valid_thread_id(t.id):
                    with _state_lock:
                        self._threads.setdefault(sid, {})[t.id] = t

    def _ensure_main(self, session_id: str, created_at: str | None) -> None:
        ts = self._threads.setdefault(session_id, {})
        if MAIN_THREAD not in ts:
            ts[MAIN_THREAD] = ChatThread(
                id=MAIN_THREAD,
                session_id=session_id,
                created_at=created_at or _utcnow_iso(),
            )

    def list(
        self, session_id: str, *, session_created_at: str | None = None
    ) -> list[ChatThread]:
        with _state_lock:
            self._ensure_main(session_id, session_created_at)
            return list(self._threads[session_id].values())

    def get(self, session_id: str, thread_id: str) -> ChatThread | None:
        with _state_lock:
            if thread_id == MAIN_THREAD:
                self._ensure_main(session_id, None)
            return self._threads.get(session_id, {}).get(thread_id)

    def create(self, session_id: str) -> ChatThread:
        with _state_lock:
            self._ensure_main(session_id, None)
            ts = self._threads[session_id]
            if len(ts) >= MAX_THREADS_PER_SESSION:
                raise ValueError(
                    f"a session keeps at most {MAX_THREADS_PER_SESSION} chats; "
                    "delete one first"
                )
            t = ChatThread(id=f"thr_{uuid.uuid4().hex[:12]}", session_id=session_id)
            ts[t.id] = t
        save_session_state()
        _mirror_thread(t)
        return t

    def rename(self, session_id: str, thread_id: str, title: str) -> ChatThread | None:
        clean = " ".join(title.split())[:THREAD_TITLE_MAX] or None
        with _state_lock:
            t = self._threads.get(session_id, {}).get(thread_id)
            if t is None:
                return None
            t.title = clean
        save_session_state()
        _mirror_thread(t)
        return t

    def delete(self, session_id: str, thread_id: str) -> bool:
        """Remove a chat and its messages. Deleting the main chat empties it."""
        with _state_lock:
            ts = self._threads.get(session_id, {})
            if thread_id not in ts:
                return False
            if thread_id == MAIN_THREAD:
                ts[thread_id].title = None
                main = ts[thread_id]
            else:
                main = None
                del ts[thread_id]
        message_store.drop(history_key(session_id, thread_id))
        save_session_state()
        _mirror_thread_delete(session_id, thread_id, main)
        return True

    def clear(self) -> None:
        with _state_lock:
            self._threads.clear()


class SessionRepository(Protocol):
    """Interface the chat layer depends on (in-memory or Postgres)."""

    def create(
        self,
        *,
        learner_id: str,
        course_id: str,
        resource_id: str,
        lesson_id: str | None,
        resource_scope: list[str],
        current_time_ms: int,
        owner_id: str | None = None,
    ) -> Session: ...
    def get(self, session_id: str) -> Session | None: ...
    def clear(self) -> None: ...


class MessageRepository(Protocol):
    """Interface the chat layer depends on (in-memory or Postgres)."""

    def append(
        self,
        session_id: str,
        role: str,
        content: str,
        citations: list[dict] | None = None,
    ) -> StoredMessage: ...
    def list_for_session(self, session_id: str) -> list[StoredMessage]: ...
    def user_texts(self, session_id: str, limit: int = 5) -> list[str]: ...
    def clear(self) -> None: ...


class InMemorySessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def snapshot(self) -> dict[str, dict]:
        # Copy under the lock: json.dumps below iterates this mapping, and it
        # must not be able to see a concurrent insert.
        with _state_lock:
            return {sid: asdict(s) for sid, s in list(self._sessions.items())}

    def restore(self, data: dict[str, dict], *, apply_ttl: bool = True) -> int:
        restored = 0
        cutoff = datetime.now(UTC) - SESSION_TTL
        for sid, row in list(data.items()):
            try:
                session = Session(
                    id=str(row["id"]),
                    learner_id=str(row["learner_id"]),
                    course_id=str(row["course_id"]),
                    resource_id=str(row["resource_id"]),
                    lesson_id=row.get("lesson_id"),
                    resource_scope=[str(x) for x in (row.get("resource_scope") or [])],
                    current_time_ms=int(row.get("current_time_ms") or 0),
                    created_at=str(row.get("created_at") or _utcnow_iso()),
                    owner_id=(str(row["owner_id"]) if row.get("owner_id") else None),
                )
            except (KeyError, TypeError, ValueError) as exc:
                log.warning("skipping unreadable session row %s: %s", sid, exc)
                continue
            created = _parse_iso(session.created_at)
            if apply_ttl and created is not None and created < cutoff:
                continue  # expired: drop rather than resurrect
            with _state_lock:
                self._sessions[session.id] = session
            restored += 1
        return restored

    def create(
        self,
        *,
        learner_id: str,
        course_id: str,
        resource_id: str,
        lesson_id: str | None,
        resource_scope: list[str],
        current_time_ms: int,
        owner_id: str | None = None,
    ) -> Session:
        session_id = f"ses_{uuid.uuid4().hex[:16]}"
        session = Session(
            id=session_id,
            learner_id=learner_id,
            course_id=course_id,
            resource_id=resource_id,
            lesson_id=lesson_id,
            resource_scope=list(resource_scope),
            current_time_ms=int(current_time_ms),
            owner_id=owner_id,
        )
        with _state_lock:
            self._sessions[session_id] = session
        save_session_state()
        _mirror_session(session)
        return session

    def get(self, session_id: str) -> Session | None:
        with _state_lock:
            return self._sessions.get(session_id)

    def clear(self) -> None:
        with _state_lock:
            self._sessions.clear()


class InMemoryMessageStore:
    def __init__(self) -> None:
        self._messages: dict[str, list[StoredMessage]] = {}

    def snapshot(self) -> dict[str, list[dict]]:
        # Copy under the lock, and copy each message list: json.dumps walks
        # both the outer mapping and the per-session lists.
        with _state_lock:
            return {
                sid: [asdict(m) for m in list(msgs)]
                for sid, msgs in list(self._messages.items())
                if msgs
            }

    def restore(self, data: dict[str, list[dict]]) -> int:
        restored = 0
        for sid, rows in list(data.items()):
            if not isinstance(rows, list):
                continue
            kept: list[StoredMessage] = []
            for row in rows:
                try:
                    kept.append(
                        StoredMessage(
                            id=str(row["id"]),
                            session_id=str(row["session_id"]),
                            role=str(row["role"]),
                            content=str(row["content"]),
                            citations=list(row.get("citations") or []),
                            created_at=str(row.get("created_at") or _utcnow_iso()),
                        )
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    log.warning("skipping unreadable message row in %s: %s", sid, exc)
            if kept:
                with _state_lock:
                    self._messages[sid] = kept
                restored += len(kept)
        return restored

    def append(
        self,
        session_id: str,
        role: str,
        content: str,
        citations: list[dict] | None = None,
    ) -> StoredMessage:
        msg = StoredMessage(
            id=f"msg_{uuid.uuid4().hex[:16]}",
            session_id=session_id,
            role=role,
            content=content,
            citations=list(citations) if citations else [],
        )
        with _state_lock:
            self._messages.setdefault(history_key(session_id), []).append(msg)
        save_session_state()
        _mirror_message(msg, current_thread.get())
        return msg

    def list_for_session(
        self, session_id: str, thread_id: str | None = None
    ) -> list[StoredMessage]:
        """The messages of one chat: ``thread_id``, else the current one."""
        with _state_lock:
            return list(self._messages.get(history_key(session_id, thread_id), []))

    def user_texts(self, session_id: str, limit: int = 5) -> list[str]:
        with _state_lock:
            texts = [
                m.content
                for m in self._messages.get(history_key(session_id), [])
                if m.role == "user"
            ]
        return texts[-limit:]

    def drop(self, key: str) -> None:
        with _state_lock:
            self._messages.pop(key, None)

    def clear(self) -> None:
        with _state_lock:
            self._messages.clear()


session_store = InMemorySessionStore()
message_store = InMemoryMessageStore()
thread_store = InMemoryThreadStore()


def _parse_iso(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


# ---------------------------------------------------------------------------
# Supabase mirror (docs/SUPABASE_PLAN.md, "API persistence strategy")
#
# Write-through and best-effort, like save_session_state: a failure warns and
# is never raised into a request. Rows carry the full serialized object in
# ``data`` so they round-trip.
# ---------------------------------------------------------------------------

_PAGE = 1000


def _owner_of(session_id: str) -> str | None:
    s = session_store.get(session_id)
    return s.owner_id if s is not None else None


def _supa_write(what: str, fn) -> None:
    client = get_client()
    if client is None:
        return
    try:
        fn(client)
    except Exception as exc:  # noqa: BLE001 - mirroring must not fail a request
        log.warning("supabase %s failed: %s", what, exc)


def _mirror_session(session: Session) -> None:
    row = {
        "id": session.id,
        "owner_id": session.owner_id,
        "course_id": session.course_id,
        "resource_id": session.resource_id,
        "subject_id": None,
        "resource_scope": list(session.resource_scope),
        "data": asdict(session),
        "created_at": session.created_at,
        "updated_at": _utcnow_iso(),
    }
    _supa_write(
        "session upsert", lambda c: c.upsert("chat_sessions", row, on_conflict="id")
    )


def _mirror_thread(t: ChatThread) -> None:
    row = {
        "id": t.id,
        "session_id": t.session_id,
        "owner_id": _owner_of(t.session_id),
        "title": t.title,
        "created_at": t.created_at,
    }
    _supa_write(
        "thread upsert",
        lambda c: c.upsert("chat_threads", row, on_conflict="session_id,id"),
    )


def _mirror_thread_delete(
    session_id: str, thread_id: str, main: ChatThread | None
) -> None:
    def go(c) -> None:
        c.delete(
            "chat_messages",
            {"session_id": f"eq.{session_id}", "thread_id": f"eq.{thread_id}"},
        )
        if main is not None:
            _mirror_thread(main)
        else:
            c.delete(
                "chat_threads",
                {"session_id": f"eq.{session_id}", "id": f"eq.{thread_id}"},
            )

    _supa_write("thread delete", go)


def _mirror_message(msg: StoredMessage, thread_id: str) -> None:
    row = {
        "id": msg.id,
        "session_id": msg.session_id,
        "thread_id": thread_id,
        "owner_id": _owner_of(msg.session_id),
        "role": msg.role,
        "content": msg.content,
        "citations": list(msg.citations),
        "data": {**asdict(msg), "thread_id": thread_id},
        "created_at": msg.created_at,
    }
    _supa_write(
        "message upsert", lambda c: c.upsert("chat_messages", row, on_conflict="id")
    )


def _select_all(client, table: str) -> list[dict]:
    rows: list[dict] = []
    offset = 0
    while True:
        page = client.select(
            table,
            {"order": "created_at.asc", "limit": str(_PAGE), "offset": str(offset)},
        )
        rows.extend(page)
        if len(page) < _PAGE:
            return rows
        offset += _PAGE


def load_sessions_from_supabase() -> int:
    """Reload sessions, chats and messages from Supabase. Never raises.

    Returns the number of sessions restored. No TTL: stored history is durable.
    """
    client = get_client()
    if client is None:
        return 0
    try:
        session_rows = _select_all(client, "chat_sessions")
        thread_rows = _select_all(client, "chat_threads")
        message_rows = _select_all(client, "chat_messages")
    except Exception as exc:  # noqa: BLE001
        log.warning("supabase session load failed: %s", exc)
        return 0

    sessions: dict[str, dict] = {}
    for r in session_rows:
        if not isinstance(r, dict) or not r.get("id"):
            continue
        data = dict(r.get("data") or {})
        data["id"] = r["id"]
        data["owner_id"] = r.get("owner_id")
        data.setdefault("created_at", r.get("created_at"))
        sessions[str(r["id"])] = data

    threads: dict[str, list[dict]] = {}
    for r in thread_rows:
        if not isinstance(r, dict) or r.get("session_id") not in sessions:
            continue
        threads.setdefault(r["session_id"], []).append(r)

    messages: dict[str, list[dict]] = {}
    for r in message_rows:
        if not isinstance(r, dict) or r.get("session_id") not in sessions:
            continue
        d = dict(r.get("data") or {})
        d.setdefault("id", r.get("id"))
        d.setdefault("session_id", r.get("session_id"))
        d.setdefault("role", r.get("role"))
        d.setdefault("content", r.get("content") or "")
        d.setdefault("citations", r.get("citations") or [])
        d.setdefault("created_at", r.get("created_at"))
        tid = r.get("thread_id") or MAIN_THREAD
        key = history_key(str(r["session_id"]), str(tid))
        messages.setdefault(key, []).append(d)

    n = session_store.restore(sessions, apply_ttl=False)
    thread_store.restore(threads)
    message_store.restore(messages)
    if n:
        log.info("restored %d sessions from supabase", n)
    return n


def save_session_state() -> None:
    """Persist sessions + messages for localhost restarts.

    Mirrors ``app.ingestion.router.save_ingest_state``: best-effort, warn on
    failure, never raise into a request path. No-op until persistence is
    enabled by the app lifespan.
    """
    if not _persistence_enabled:
        return
    try:
        SESSION_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        SESSION_STATE_PATH.write_text(
            json.dumps(
                {
                    "sessions": session_store.snapshot(),
                    "messages": message_store.snapshot(),
                    "threads": thread_store.snapshot(),
                }
            ),
            encoding="utf-8",
        )
    except OSError as exc:
        log.warning("session state save failed: %s", exc)


def load_session_state() -> int:
    """Reload sessions/messages saved by ``save_session_state``.

    Returns the number of sessions restored. Unknown or malformed rows are
    skipped — a missing session must still 404 rather than be invented.
    """
    if not SESSION_STATE_PATH.is_file():
        return 0
    try:
        data = json.loads(SESSION_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("ignoring unreadable session state: %s", exc)
        return 0
    if not isinstance(data, dict):
        log.warning("ignoring malformed session state (not an object)")
        return 0
    sessions = data.get("sessions")
    messages = data.get("messages")
    n_sessions = (
        session_store.restore(sessions) if isinstance(sessions, dict) else 0
    )
    if isinstance(messages, dict):
        message_store.restore(messages)
    threads = data.get("threads")
    if isinstance(threads, dict):
        thread_store.restore(threads)
    if n_sessions:
        log.info("restored %d sessions from local session state", n_sessions)
    return n_sessions


def get_session_store() -> SessionRepository:
    """Return the active session backend (in-memory behind the repo interface)."""
    return session_store


def get_message_store() -> MessageRepository:
    """Return the active message backend (in-memory behind the repo interface)."""
    return message_store


def reset_stores() -> None:
    session_store.clear()
    message_store.clear()
    thread_store.clear()
