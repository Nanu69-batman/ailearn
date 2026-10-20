"""Subject store: dataclass, JSON persistence, and the retrieval scope contract.

A subject groups existing resources by *reference* (``resource_id``) — it never
duplicates media. The store is process-local and mirrored to
``data/subjects.json``, reusing the exact idiom of ``app.chat.sessions``
(``save_session_state`` / ``load_session_state``): best-effort write, warn on
failure, never raise into a request path, load on startup, drop expired rows,
skip malformed rows.

Two safety properties this module exists to guarantee:

1. **No unverifiable resource id may enter a scope.** ``attach_resource``
   refuses an id that cannot be proven to exist in the retrieval registry.
   A scope allow-list seeded with a plausible-looking id (the failure mode
   recorded in ``docs/HANDOFF.md`` §2.1) silently matches no chunk and every
   answer is then refused — or worse, an unchecked id widens the grant. The
   check fails *closed*: if the registry cannot be read, attachment is
   rejected rather than trusted.

2. **The strict-T timestamp gate is independent of this scope.** Nothing here
   filters on time. ``resolve_subject_resource_scope`` returns *which resources
   may be read*; the retrieval layer must still keep only passages with
   ``end_ms <= T``. Scoping wider in time is never this module's business, and
   this module must never be used as a substitute for that gate.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)

#: Guards the module-level subject map *and* the read-modify-write of the state
#: file. FastAPI serves each request on a worker thread, so two concurrent
#: attaches would otherwise both snapshot, then both write — the second write
#: silently discarding the first attach. The snapshot AND the atomic replace
#: happen inside this one lock so that race cannot reopen. ``RLock`` because
#: the mutating methods call ``save_subject_state()`` while already held.
_state_lock = threading.RLock()

#: Subjects older than this are dropped on load, so a long-lived dev box does
#: not resurrect abandoned organisation forever.
SUBJECT_TTL = timedelta(days=30)

#: Same seam style as ``chat.sessions.SESSION_STATE_PATH``: relative, so it
#: resolves against ``apps/api/`` (the API's working dir). Tests monkeypatch
#: this to a tmp_path; persistence stays OFF until the lifespan enables it, so
#: no test run can write to the developer's real ``data/``.
SUBJECT_STATE_PATH = Path("data/subjects.json")

_persistence_enabled = False


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def set_persistence_enabled(enabled: bool) -> None:
    """Enable/disable mirroring subjects to ``SUBJECT_STATE_PATH``."""
    global _persistence_enabled
    _persistence_enabled = enabled


def persistence_enabled() -> bool:
    return _persistence_enabled


@dataclass
class Subject:
    """A named group of existing resources, optionally tied to a course."""

    id: str
    name: str
    description: str = ""
    course_id: str | None = None
    #: ``resource_id``s that exist in the retrieval registry. Order is
    #: insertion order so a listing reads naturally; membership is what
    #: matters, not order.
    resource_ids: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=_utcnow_iso)


class UnknownResourceError(ValueError):
    """Raised when a ``resource_id`` cannot be proven to exist.

    Deliberately *not* a ``LookupError``: attach must be able to tell a bad
    reference (422) from a bad subject (404), and these must not be catchable
    by the same handler.
    """


class UnknownSubjectError(LookupError):
    """Raised by store methods when ``subject_id`` does not exist."""


# ---------------------------------------------------------------------------
# Resource existence
# ---------------------------------------------------------------------------


def _known_resource_ids(course_id: str | None) -> set[str] | None:
    """Resource ids the retrieval registry can vouch for, or ``None``.

    ``None`` means *could not determine* — which callers must treat as "no"
    (fail closed), not as "everything".

    Read-only use of the retrieval store. This module never writes to the
    registry and never re-implements indexing; it only asks what exists. The
    lookup order prefers the registry's own public API and degrades to a
    metadata read of its Chroma collection, so a rename in ``app.retrieval``
    costs a warning rather than an exception in a request path.
    """
    from app.retrieval import registry

    try:
        store = registry.get_shared_store()
    except Exception as exc:  # noqa: BLE001 - unverifiable must not 500
        log.warning("subject resource check: registry unavailable (%s)", exc)
        return None

    # 1. An explicit global listing, if the retrieval layer exposes one.
    lister = getattr(store, "resource_ids", None)
    if callable(lister):
        try:
            return {str(x) for x in lister()}
        except Exception as exc:  # noqa: BLE001
            log.warning("subject resource check: resource_ids() failed (%s)", exc)
            return None

    # 2. The documented per-course listing, when the subject names a course.
    if course_id:
        try:
            rows = store.resources(course_id)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "subject resource check: resources(%s) failed (%s)", course_id, exc
            )
            return None
        return {str(row.get("id")) for row in rows or [] if row.get("id")}

    # 3. Course-less fallback: read the registry's stored metadata. This is
    #    the same data `resources()` reads; it is a compatibility seam, not a
    #    second source of truth.
    collection = getattr(store, "_collection", None)
    if collection is None:
        log.warning(
            "subject resource check: store exposes no resource listing; "
            "refusing to attach unverifiable resources"
        )
        return None
    try:
        got = collection.get(include=["metadatas"])
    except Exception as exc:  # noqa: BLE001
        log.warning("subject resource check: metadata read failed (%s)", exc)
        return None
    return {
        str(meta["resource_id"])
        for meta in (got.get("metadatas") or [])
        if meta and meta.get("resource_id")
    }


def resource_exists(resource_id: str, course_id: str | None = None) -> bool:
    """True only if ``resource_id`` is proven present in the registry."""
    known = _known_resource_ids(course_id)
    if known is None:
        return False
    return resource_id in known


# ---------------------------------------------------------------------------
# Resource catalogue for a subject
# ---------------------------------------------------------------------------

#: The exact key set ``GET /courses/{id}/resources`` emits. A subject listing
#: must be shape-identical so the web client renders a subject's materials with
#: the same component it uses for a course's, with no second lookup.
_RESOURCE_KEYS = (
    "id",
    "type",
    "provider",
    "youtube_id",
    "title",
    "status",
    "asset_name",
)


def _entry_from_metadata(meta: dict) -> dict:
    """Build one catalogue entry in the course-listing shape.

    Mirrors ``ChromaVectorStore.resources`` field for field, including the
    ``video_id`` -> ``youtube_id`` rename the web client depends on and the
    rule that a title is never invented (no filename -> ``None``).
    """
    provider_video_id = meta.get("video_id") or None
    return {
        "id": meta.get("resource_id"),
        "type": meta.get("source_type", ""),
        "provider": "youtube" if provider_video_id else "file",
        "youtube_id": provider_video_id,
        "title": meta.get("title") or None,
        "status": "ready",
        "asset_name": _stored_asset_name(meta.get("resource_id")),
    }


def _stored_asset_name(resource_id) -> str | None:
    """The on-disk asset for ``resource_id``, mirroring the course endpoint.

    ``GET /courses/{id}/resources`` resolves ``asset_name`` by globbing the
    upload dir for ``{resource_id}.*`` (``app.ingestion.router``). The
    registry row itself never carries it, so every subject-side entry must
    derive it the same way — otherwise the web client hides file videos.
    """
    if not resource_id:
        return None
    try:
        from app.ingestion.router import UPLOAD_DIR

        matches = sorted(UPLOAD_DIR.glob(f"{resource_id}.*"))
    except Exception as exc:  # noqa: BLE001 - a listing must not 500
        log.warning("subject resource listing: asset lookup failed (%s)", exc)
        return None
    return matches[0].name if matches else None


def _entries_via_collection(
    store, wanted: list[str], found: dict[str, dict]
) -> dict[str, dict]:
    """Metadata fallback for ids the public per-course listing missed."""
    collection = getattr(store, "_collection", None)
    if collection is None:
        return found
    remaining = set(wanted) - set(found)
    if not remaining:
        return found
    try:
        got = collection.get(include=["metadatas"])
    except Exception as exc:  # noqa: BLE001 - a listing must not 500
        log.warning("subject resource listing: metadata read failed (%s)", exc)
        return found
    for meta in got.get("metadatas") or []:
        if not meta:
            continue
        rid = meta.get("resource_id")
        if rid not in remaining:
            continue
        entry = found.get(rid)
        if entry is None:
            found[rid] = _entry_from_metadata(meta)
            continue
        # Keep the first real title rather than letting an untitled chunk
        # overwrite it (a partial re-ingest can leave some chunks without one).
        if not entry["title"] and meta.get("title"):
            entry["title"] = str(meta["title"])
    return found


def list_subject_resources(subject_id: str) -> list[dict] | None:
    """The catalogue entries for the resources attached to ``subject_id``.

    ``None`` means *the subject does not exist* — the same distinction from
    ``[]`` that :func:`resolve_subject_resource_scope` draws, for the same
    reason: "unknown" and "known but empty" must never collapse into one
    answer. The router turns ``None`` into 404 and ``[]`` into a 200 with an
    empty ``resources`` list.

    Order is the subject's own attachment order, not the registry's. A
    subject may attach resources from more than one course, so this must never
    be implemented as "look up the subject's course and list that course" —
    that silently returns the *course's* materials and makes two subjects on
    one course render identical lists.

    Ids that no longer resolve in the retrieval registry are skipped, not
    fatal: the registry can be re-indexed or a resource deleted underneath a
    long-lived subject, and a listing must degrade to a shorter list rather
    than raise into the request path.
    """
    subject = subject_store.get(subject_id)
    if subject is None:
        return None
    # Copy under the lock: a concurrent attach appends to ``resource_ids`` in
    # place, and this list must not be read mid-mutation.
    with _state_lock:
        resource_ids = list(subject.resource_ids)
        course_id = subject.course_id

    if not resource_ids:
        return []

    from app.retrieval import registry

    try:
        store = registry.get_shared_store()
    except Exception as exc:  # noqa: BLE001 - unverifiable must not 500
        log.warning("subject resource listing: registry unavailable (%s)", exc)
        return []

    found: dict[str, dict] = {}
    # The documented per-course listing owns the shape when it applies.
    if course_id:
        try:
            for row in store.resources(course_id) or []:
                rid = row.get("id")
                if rid:
                    found[rid] = {k: row.get(k) for k in _RESOURCE_KEYS}
                    # Present only for windowed-ingest resources; legacy rows
                    # keep the exact old shape.
                    if "processed_through_ms" in row:
                        found[rid]["processed_through_ms"] = row[
                            "processed_through_ms"
                        ]
                    if "visual_through_ms" in row:
                        found[rid]["visual_through_ms"] = row["visual_through_ms"]
                    # The registry row never carries asset_name (the course
                    # router decorates it from UPLOAD_DIR) — do the same here
                    # or the web client hides file videos for subjects.
                    if not found[rid].get("asset_name"):
                        found[rid]["asset_name"] = _stored_asset_name(rid)
        except Exception as exc:  # noqa: BLE001 - a listing must not 500
            log.warning(
                "subject resource listing: resources(%s) failed (%s)", course_id, exc
            )
    _entries_via_collection(store, resource_ids, found)

    ordered = [found[rid] for rid in resource_ids if rid in found]
    missing = [rid for rid in resource_ids if rid not in found]
    if missing:
        log.warning(
            "subject %s references %d resource id(s) absent from the registry: %s",
            subject_id,
            len(missing),
            ", ".join(missing),
        )
    return ordered


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class InMemorySubjectStore:
    """Process-local subject map with JSON mirroring."""

    def __init__(self) -> None:
        self._subjects: dict[str, Subject] = {}

    def snapshot(self) -> dict[str, dict]:
        # Copy under the lock: json.dumps walks this mapping, and it must not
        # observe a concurrent insert.
        with _state_lock:
            return {sid: asdict(s) for sid, s in list(self._subjects.items())}

    def restore(self, data: dict[str, dict]) -> int:
        restored = 0
        cutoff = datetime.now(timezone.utc) - SUBJECT_TTL
        for sid, row in list(data.items()):
            try:
                subject = Subject(
                    id=str(row["id"]),
                    name=str(row["name"]),
                    description=str(row.get("description") or ""),
                    course_id=row.get("course_id"),
                    resource_ids=[
                        str(x) for x in (row.get("resource_ids") or [])
                    ],
                    created_at=str(row.get("created_at") or _utcnow_iso()),
                )
            except (KeyError, TypeError, ValueError) as exc:
                log.warning("skipping unreadable subject row %s: %s", sid, exc)
                continue
            created = _parse_iso(subject.created_at)
            if created is not None and created < cutoff:
                continue  # expired: drop rather than resurrect
            with _state_lock:
                self._subjects[subject.id] = subject
            restored += 1
        return restored

    def create(
        self, *, name: str, description: str = "", course_id: str | None = None
    ) -> Subject:
        subject = Subject(
            id=f"sub_{uuid.uuid4().hex[:16]}",
            name=name,
            description=description,
            course_id=course_id,
        )
        with _state_lock:
            self._subjects[subject.id] = subject
            save_subject_state()
        return subject

    def get(self, subject_id: str) -> Subject | None:
        with _state_lock:
            return self._subjects.get(subject_id)

    def list_all(self) -> list[Subject]:
        with _state_lock:
            return sorted(self._subjects.values(), key=lambda s: s.created_at)

    def update(
        self,
        subject_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> Subject | None:
        with _state_lock:
            subject = self._subjects.get(subject_id)
            if subject is None:
                return None
            if name is not None:
                subject.name = name
            if description is not None:
                subject.description = description
            save_subject_state()
            return subject

    def delete(self, subject_id: str) -> bool:
        """Remove the subject.

        The underlying resources stay in the retrieval registry — deleting a
        subject removes the grouping, never the indexed material.
        """
        with _state_lock:
            if self._subjects.pop(subject_id, None) is None:
                return False
            save_subject_state()
            return True

    def attach_resource(self, subject_id: str, resource_id: str) -> Subject:
        """Add a resource reference, rejecting ids that do not exist.

        Raises ``UnknownResourceError`` for an id the registry cannot vouch
        for, so a subject can never hold a scope entry that resolves to no
        chunk (HANDOFF §2.1).
        """
        with _state_lock:
            subject = self._subjects.get(subject_id)
            if subject is None:
                raise UnknownSubjectError(subject_id)
            if not resource_exists(resource_id, subject.course_id):
                raise UnknownResourceError(resource_id)
            if resource_id not in subject.resource_ids:
                subject.resource_ids.append(resource_id)
                save_subject_state()
            return subject

    def detach_resource(self, subject_id: str, resource_id: str) -> Subject | None:
        """Remove a resource reference. ``None`` if the subject is unknown."""
        with _state_lock:
            subject = self._subjects.get(subject_id)
            if subject is None:
                return None
            if resource_id in subject.resource_ids:
                subject.resource_ids.remove(resource_id)
                save_subject_state()
            return subject

    def resource_ids(self, subject_id: str) -> list[str] | None:
        """A subject's resource ids, or ``None`` if the subject is unknown.

        ``None`` and ``[]`` are deliberately distinct and must stay so — see
        :func:`resolve_subject_resource_scope`.
        """
        with _state_lock:
            subject = self._subjects.get(subject_id)
            if subject is None:
                return None
            return list(subject.resource_ids)

    def clear(self) -> None:
        with _state_lock:
            self._subjects.clear()


subject_store = InMemorySubjectStore()


# ---------------------------------------------------------------------------
# Retrieval scope contract
# ---------------------------------------------------------------------------


def resolve_subject_resource_scope(subject_id: str) -> list[str] | None:
    """The retrieval scope allow-list for a subject.

    Returns the ``resource_id``s belonging to ``subject_id``.

    Semantics — the two cases mean opposite things and must never be confused:

    * ``None``  -> **unscoped.** The subject does not exist, so no scope was
      requested. A retrieval caller that was given a subject id must treat
      this as a configuration error (and, per the fail-closed rule in
      ``app.retrieval.store.build_where``, must NOT fall back to "search
      everything"): an unknown subject must not become an unrestricted search.
    * ``[]``    -> **scoped to nothing.** The subject exists but has no
      materials, so nothing is retrievable. Passing this to the store yields
      an empty result — the correct answer, and different from refusal to
      answer at all.

    Returning ``[]`` for an unknown subject would silently black-hole
    retrieval; returning ``None`` for an empty subject would silently widen it
    to the whole corpus. That inversion is the single most dangerous bug this
    helper can have, which is why both are covered by tests.

    Independent of time: this is a *which resources* allow-list only. The
    strict-T gate (``end_ms <= T``) still applies to every retrieved passage
    and is not relaxed, replaced, or inferred by anything in this module.
    """
    return subject_store.resource_ids(subject_id)


# ---------------------------------------------------------------------------
# Persistence (same idiom as app.chat.sessions)
# ---------------------------------------------------------------------------


def _parse_iso(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def save_subject_state() -> None:
    """Mirror subjects to ``SUBJECT_STATE_PATH``. Best-effort, never raises.

    Called while ``_state_lock`` is already held by the mutating method, so
    snapshot-then-write is atomic against other attaches. The file itself is
    written to a sibling temp path and ``os.replace``d, so a crash mid-write
    cannot leave a truncated state file behind.
    """
    if not _persistence_enabled:
        return
    try:
        data = subject_store.snapshot()
        SUBJECT_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = SUBJECT_STATE_PATH.with_suffix(
            SUBJECT_STATE_PATH.suffix + f".{os.getpid()}.tmp"
        )
        tmp.write_text(json.dumps({"subjects": data}), encoding="utf-8")
        os.replace(tmp, SUBJECT_STATE_PATH)
    except OSError as exc:
        log.warning("subject state save failed: %s", exc)


def load_subject_state() -> int:
    """Reload subjects saved by ``save_subject_state``.

    Returns the number restored. Expired and malformed rows are skipped: a
    subject that cannot be read must 404, never be invented.
    """
    if not SUBJECT_STATE_PATH.is_file():
        return 0
    try:
        data = json.loads(SUBJECT_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("ignoring unreadable subject state: %s", exc)
        return 0
    if not isinstance(data, dict):
        log.warning("ignoring malformed subject state (not an object)")
        return 0
    rows = data.get("subjects")
    restored = subject_store.restore(rows) if isinstance(rows, dict) else 0
    if restored:
        log.info("restored %d subjects from local subject state", restored)
    return restored


def reset_store() -> None:
    """Test helper: drop all subjects from memory."""
    subject_store.clear()