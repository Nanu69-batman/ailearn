"""Process-wide shared store.

The Chroma ``PersistentClient`` owns an on-disk HNSW index under ``data/``.
Creating two clients on the same directory in one process is not safe, so the
client is created once and shared -- which is exactly why ``ChatService`` and
the ingestion router must both go through :func:`get_shared_store`.

No snapshot pickle: the old hand-built store serialised itself to
``data/store_snapshot.pkl``. The new store's durable state *is* the Chroma
directory, so the snapshot format (and its restore path) is obsolete rather
than merely unwritten.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

log = logging.getLogger(__name__)

#: Default on-disk location for the Chroma index, overridable so tests can
#: point at a tmp_path and never touch the developer's ``data/``.
CHROMA_PATH_ENV = "CHROMA_PATH"

_LOCK = threading.Lock()
_STORE = None


def default_chroma_path() -> Path:
    override = os.environ.get(CHROMA_PATH_ENV)
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent.parent / "data" / "chroma"


def get_shared_store():
    """Return the process-wide store, creating it on first use."""
    global _STORE
    if _STORE is not None:
        return _STORE
    with _LOCK:
        if _STORE is None:
            from app.retrieval.store import ChromaVectorStore

            path = default_chroma_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            _STORE = ChromaVectorStore(path=path)
    return _STORE


def set_shared_store(store) -> None:
    """Override the shared store (tests)."""
    global _STORE
    with _LOCK:
        _STORE = store


def reset_shared_store() -> None:
    """Drop the cached client. Next access reopens the same directory."""
    global _STORE
    with _LOCK:
        _STORE = None