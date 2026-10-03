"""Disk caches for billable API calls (demo cost control).

Covers the three expensive call types: media transcription (one big call per
file), embeddings (one call per chunk text), and chat completions. Keys are
content hashes, so identical inputs never pay twice — re-ingests, restarts,
and repeated questions hit disk instead of the API.

Safety: the chat key includes the model, mode, full prompt (which embeds the
retrieved passage IDs, timestamp scope, and history), so a cache hit implies
identical evidence. Nothing time-sensitive is cached. Disable with
CACHE_ENABLED=false.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_CACHE_DIR = Path("data/caches")


def sha1hex(*parts: str) -> str:
    h = hashlib.sha1()
    for part in parts:
        h.update(part.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


class DiskCache:
    """Tiny JSON file-backed key/value cache with atomic saves."""

    def __init__(self, name: str, cache_dir: str | Path | None = None) -> None:
        base = Path(cache_dir) if cache_dir is not None else DEFAULT_CACHE_DIR
        self.path = base / f"{name}.json"
        self._data: dict[str, object] = {}
        self._loaded = False
        self.hits = 0
        self.misses = 0

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            if self.path.is_file():
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("ignoring unreadable cache %s: %s", self.path, exc)
            self._data = {}

    def get(self, key: str):
        self._load()
        if key in self._data:
            self.hits += 1
            return self._data[key]
        self.misses += 1
        return None

    def set(self, key: str, value: object) -> None:
        self._load()
        self._data[key] = value
        self._save()

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(
                dir=str(self.path.parent), prefix=self.path.name + "."
            )
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh)
            os.replace(tmp, self.path)
        except OSError as exc:
            log.warning("cache save failed for %s: %s", self.path, exc)

    def stats(self) -> dict:
        return {"path": str(self.path), "entries": len(self._data),
                "hits": self.hits, "misses": self.misses}


_caches: dict[str, DiskCache] = {}


def get_cache(name: str) -> DiskCache:
    """Return the process-wide cache for ``name`` (reset seam below)."""
    if name not in _caches:
        _caches[name] = DiskCache(name)
    return _caches[name]


def reset_caches() -> None:
    """Drop all cached handles (tests only)."""
    _caches.clear()


def cache_enabled() -> bool:
    from app.config import settings

    return bool(settings.cache_enabled)
