"""Shared in-memory vector store singleton.

Single seam so file/YouTube ingestion and chat retrieval observe the same
chunks in tests and demo (no database). Production swaps this for a
Postgres/pgvector backend; nothing else in the retrieval package is touched.

Localhost persistence: the store can snapshot its chunks to disk
(``save_snapshot``) and reload them on startup (``load_snapshot``) so a
server restart does not wipe already-paid-for ingestions. Snapshots are a
dev convenience only — the source of truth remains re-ingestion.
"""

from __future__ import annotations

import logging
import pickle
from pathlib import Path

from app.retrieval.store import InMemoryVectorStore

log = logging.getLogger(__name__)

_shared_store: InMemoryVectorStore | None = None

DEFAULT_SNAPSHOT_PATH = Path("data/store_snapshot.pkl")

# Test seam (mirrors ingestion router TEST_DATA_DIR): when set, snapshot
# helpers use this path instead of the default, so tests never touch dev data.
SNAPSHOT_PATH: Path | None = None


def _snapshot_target(explicit: str | Path | None) -> Path:
    if explicit is not None:
        return Path(explicit)
    if SNAPSHOT_PATH is not None:
        return SNAPSHOT_PATH
    return DEFAULT_SNAPSHOT_PATH


def get_shared_store() -> InMemoryVectorStore:
    """Return the process-wide in-memory store, creating it on first use."""
    global _shared_store
    if _shared_store is None:
        _shared_store = InMemoryVectorStore([])
    return _shared_store


def reset_shared_store() -> InMemoryVectorStore:
    """Drop all indexed chunks (tests/dev only); returns the fresh store."""
    global _shared_store
    _shared_store = InMemoryVectorStore([])
    return _shared_store


def save_snapshot(path: str | Path | None = None) -> int:
    """Persist shared-store chunks to disk; returns chunk count. Best-effort."""
    store = get_shared_store()
    target = _snapshot_target(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "wb") as fh:
        pickle.dump(list(store._chunks), fh)
    log.info("saved %d chunks to %s", len(store._chunks), target)
    return len(store._chunks)


def load_snapshot(path: str | Path | None = None) -> int:
    """Reload snapshot into the shared store; returns chunk count (0 if none)."""
    global _shared_store
    target = _snapshot_target(path)
    if not target.is_file():
        return 0
    try:
        with open(target, "rb") as fh:
            chunks = pickle.load(fh)
    except Exception as exc:  # noqa: BLE001 - corrupt snapshot must not break startup
        log.warning("ignoring unreadable snapshot %s: %s", target, exc)
        return 0
    _shared_store = InMemoryVectorStore(list(chunks))
    log.info("loaded %d chunks from %s", len(chunks), target)
    return len(chunks)
