"""Class store: a teacher's class, its students, its subjects and a join code.

Same persistence idiom as ``app.subjects.store``: process-local map, mirrored
to ``data/classes.json`` (best-effort, never raises) and, when Supabase is
configured, to the ``classes`` table (one jsonb row per class).

A class references subjects by id; the subjects stay owned by the teacher.
Students read them through class membership and can never write to them.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from app.supa.client import get_client

log = logging.getLogger(__name__)

_lock = threading.RLock()
CLASS_STATE_PATH = Path("data/classes.json")
_persistence_enabled = False

#: Unambiguous characters only (no 0/O, 1/I/L), so a code read aloud works.
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _new_code() -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(CODE_LENGTH))


def set_persistence_enabled(enabled: bool) -> None:
    global _persistence_enabled
    _persistence_enabled = enabled


@dataclass
class Member:
    id: str
    name: str | None = None
    email: str | None = None
    joined_at: str = field(default_factory=_now)


@dataclass
class Klass:
    id: str
    name: str
    teacher_id: str
    teacher_name: str | None = None
    description: str = ""
    join_code: str = field(default_factory=_new_code)
    members: list[Member] = field(default_factory=list)
    subject_ids: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=_now)

    def member_ids(self) -> set[str]:
        return {m.id for m in self.members}


def _from_row(row: dict) -> Klass:
    return Klass(
        id=str(row["id"]),
        name=str(row["name"]),
        teacher_id=str(row["teacher_id"]),
        teacher_name=row.get("teacher_name"),
        description=str(row.get("description") or ""),
        join_code=str(row.get("join_code") or _new_code()),
        members=[
            Member(
                id=str(m["id"]),
                name=m.get("name"),
                email=m.get("email"),
                joined_at=str(m.get("joined_at") or _now()),
            )
            for m in (row.get("members") or [])
            if isinstance(m, dict) and m.get("id")
        ],
        subject_ids=[str(s) for s in (row.get("subject_ids") or [])],
        created_at=str(row.get("created_at") or _now()),
    )


class ClassStore:
    def __init__(self) -> None:
        self._classes: dict[str, Klass] = {}

    # -- reads -------------------------------------------------------------

    def get(self, class_id: str) -> Klass | None:
        with _lock:
            return self._classes.get(class_id)

    def by_code(self, code: str) -> Klass | None:
        code = code.strip().upper()
        with _lock:
            return next((c for c in self._classes.values() if c.join_code == code), None)

    def for_teacher(self, teacher_id: str) -> list[Klass]:
        with _lock:
            return sorted(
                (c for c in self._classes.values() if c.teacher_id == teacher_id),
                key=lambda c: c.created_at,
            )

    def for_student(self, student_id: str) -> list[Klass]:
        with _lock:
            return sorted(
                (c for c in self._classes.values() if student_id in c.member_ids()),
                key=lambda c: c.created_at,
            )

    def class_of_subject(self, subject_id: str) -> Klass | None:
        with _lock:
            return next(
                (c for c in self._classes.values() if subject_id in c.subject_ids), None
            )

    def student_can_read_subject(self, user_id: str, subject_id: str) -> bool:
        with _lock:
            return any(
                subject_id in c.subject_ids and user_id in c.member_ids()
                for c in self._classes.values()
            )

    # -- writes ------------------------------------------------------------

    def create(
        self, *, name: str, description: str, teacher_id: str, teacher_name: str | None
    ) -> Klass:
        with _lock:
            code = _new_code()
            while self.by_code(code) is not None:
                code = _new_code()
            klass = Klass(
                id=f"cls_{uuid.uuid4().hex[:12]}",
                name=name,
                description=description,
                teacher_id=teacher_id,
                teacher_name=teacher_name,
                join_code=code,
            )
            self._classes[klass.id] = klass
            self._save(klass)
            return klass

    def update(self, klass: Klass, *, name: str | None, description: str | None) -> Klass:
        with _lock:
            if name is not None:
                klass.name = name
            if description is not None:
                klass.description = description
            self._save(klass)
            return klass

    def delete(self, class_id: str) -> bool:
        with _lock:
            if self._classes.pop(class_id, None) is None:
                return False
            save_state()
            _mirror_delete(class_id)
            return True

    def regenerate_code(self, klass: Klass) -> Klass:
        with _lock:
            code = _new_code()
            while self.by_code(code) is not None:
                code = _new_code()
            klass.join_code = code
            self._save(klass)
            return klass

    def add_member(self, klass: Klass, member: Member) -> Klass:
        with _lock:
            if member.id not in klass.member_ids():
                klass.members.append(member)
                self._save(klass)
            return klass

    def remove_member(self, klass: Klass, member_id: str) -> Klass:
        with _lock:
            klass.members = [m for m in klass.members if m.id != member_id]
            self._save(klass)
            return klass

    def add_subject(self, klass: Klass, subject_id: str) -> Klass:
        with _lock:
            if subject_id not in klass.subject_ids:
                klass.subject_ids.append(subject_id)
                self._save(klass)
            return klass

    def remove_subject(self, subject_id: str) -> None:
        """Drop ``subject_id`` from every class (subject deleted or unlinked)."""
        with _lock:
            for klass in self._classes.values():
                if subject_id in klass.subject_ids:
                    klass.subject_ids.remove(subject_id)
                    self._save(klass)

    def _save(self, klass: Klass) -> None:
        save_state()
        _mirror(klass)

    # -- persistence -------------------------------------------------------

    def snapshot(self) -> dict[str, dict]:
        with _lock:
            return {cid: asdict(c) for cid, c in self._classes.items()}

    def restore(self, rows: dict[str, dict]) -> int:
        restored = 0
        for cid, row in rows.items():
            try:
                klass = _from_row(row)
            except (KeyError, TypeError, ValueError) as exc:
                log.warning("skipping unreadable class row %s: %s", cid, exc)
                continue
            with _lock:
                self._classes[klass.id] = klass
            restored += 1
        return restored

    def clear(self) -> None:
        with _lock:
            self._classes.clear()


class_store = ClassStore()


def save_state() -> None:
    if not _persistence_enabled:
        return
    try:
        data = class_store.snapshot()
        CLASS_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = CLASS_STATE_PATH.with_suffix(f".json.{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"classes": data}), encoding="utf-8")
        os.replace(tmp, CLASS_STATE_PATH)
    except OSError as exc:
        log.warning("class state save failed: %s", exc)


def load_state() -> int:
    if not CLASS_STATE_PATH.is_file():
        return 0
    try:
        data = json.loads(CLASS_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("ignoring unreadable class state: %s", exc)
        return 0
    rows = data.get("classes") if isinstance(data, dict) else None
    return class_store.restore(rows) if isinstance(rows, dict) else 0


# -- Supabase mirror (table ``classes``: id, teacher_id, join_code, data) ----


def _mirror(klass: Klass) -> None:
    client = get_client()
    if client is None:
        return
    try:
        client.upsert(
            "classes",
            {
                "id": klass.id,
                "teacher_id": klass.teacher_id,
                "join_code": klass.join_code,
                "data": asdict(klass),
                "updated_at": _now(),
            },
            on_conflict="id",
        )
    except Exception as exc:  # noqa: BLE001 - mirroring must not fail a request
        log.warning("supabase class upsert failed: %s", exc)


def _mirror_delete(class_id: str) -> None:
    client = get_client()
    if client is None:
        return
    try:
        client.delete("classes", {"id": f"eq.{class_id}"})
    except Exception as exc:  # noqa: BLE001
        log.warning("supabase class delete failed: %s", exc)


def load_from_supabase() -> int:
    client = get_client()
    if client is None:
        return 0
    try:
        rows = client.select("classes", {"limit": "5000"}, columns="id,data")
    except Exception as exc:  # noqa: BLE001
        log.warning("supabase class load failed: %s", exc)
        return 0
    data = {
        str(r["id"]): r["data"]
        for r in rows
        if isinstance(r, dict) and r.get("id") and isinstance(r.get("data"), dict)
    }
    restored = class_store.restore(data)
    if restored:
        log.info("restored %d classes from supabase", restored)
    return restored


def reset_store() -> None:
    class_store.clear()
