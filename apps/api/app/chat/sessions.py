"""In-memory session/message stores (Postgres-backed in production).

The API layer stays stateless: these singletons are process-local for the
prototype/tests. Production swaps them for SQLAlchemy repositories behind the
same method names (see ``SessionRepository`` / ``MessageRepository``).

Backend selection:
  USE_IN_MEMORY=true  (default) -> process-local stores, no DB required.
  USE_IN_MEMORY=false + a real reachable DATABASE_URL -> Postgres-backed
  repos exposing the same methods. Tests always run with USE_IN_MEMORY=true
  so nothing requires network/DB access.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


@dataclass
class StoredMessage:
    id: str
    session_id: str
    role: str  # "user" | "assistant"
    content: str
    citations: list[dict] = field(default_factory=list)
    created_at: str = field(default_factory=_utcnow_iso)


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

    def create(
        self,
        *,
        learner_id: str,
        course_id: str,
        resource_id: str,
        lesson_id: str | None,
        resource_scope: list[str],
        current_time_ms: int,
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
        )
        self._sessions[session_id] = session
        return session

    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def clear(self) -> None:
        self._sessions.clear()


class InMemoryMessageStore:
    def __init__(self) -> None:
        self._messages: dict[str, list[StoredMessage]] = {}

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
        self._messages.setdefault(session_id, []).append(msg)
        return msg

    def list_for_session(self, session_id: str) -> list[StoredMessage]:
        return list(self._messages.get(session_id, []))

    def user_texts(self, session_id: str, limit: int = 5) -> list[str]:
        texts = [
            m.content for m in self._messages.get(session_id, []) if m.role == "user"
        ]
        return texts[-limit:]

    def clear(self) -> None:
        self._messages.clear()


session_store = InMemorySessionStore()
message_store = InMemoryMessageStore()


def get_session_store() -> SessionRepository:
    """Return the active session backend (in-memory behind the repo interface)."""
    return session_store


def get_message_store() -> MessageRepository:
    """Return the active message backend (in-memory behind the repo interface)."""
    return message_store


def reset_stores() -> None:
    session_store.clear()
    message_store.clear()
