"""Re-embed the local store snapshot with the *currently active* embedder.

Why this exists
---------------
``data/store_snapshot.pkl`` was written when ``EMBEDDING_API_KEY`` was set, so
its chunks carry 3072-dim Gemini vectors. Query time resolved a different
provider, so queries were 1536-dim and every cosine score was computed across
mismatched widths. Re-indexing with the active provider makes the widths agree
without hand-editing the pickle.

How it re-indexes (the project's normal path, not a hand-built pickle)
---------------------------------------------------------------------
For every resource in ``data/ingest_state.json``:

  1. segments
     * non-video -> ``app.ingestion.extractors.extract`` (pymupdf, offline)
     * video      -> the transcript disk cache that
       ``app.ingestion.transcribe.transcribe_with_gemini`` itself reads, passed
       through ``validate_transcript_payload`` + ``_to_segments`` -- i.e. exactly
       what a cache hit inside the transcription path returns. Re-transcribing
       needs ``LLM_API_KEY``; the cache is the offline equivalent of the same
       input.
  2. ``app.ingestion.jobs._chunk_segments`` -- the same chunker the ingestion
     router and jobs use, so chunk ids/texts/timestamps are reproduced
     bit-for-bit (ids are hashes of resource_id + segment key + text).
  3. ``app.ingestion.embedder.get_default_embedder()`` -- the single source of
     truth for the provider, shared with retrieval.
  4. ``InMemoryVectorStore.index()`` then
     ``app.retrieval.registry.save_snapshot()`` -- the store and the snapshot
     writer the app itself uses.

Chunking is deterministic, so chunk ids are unchanged: citations minted against
the previous snapshot stay valid.

Safety
------
Refuses to run if any chunk it would write disagrees with the existing snapshot
on ``(id, text, start_ms, end_ms)``; the old snapshot is copied to
``store_snapshot.pkl.bak`` before the first write and is never deleted.

Usage:  python scripts/reindex_snapshot.py   (from apps/api)
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.cache import cache_enabled, get_cache
from app.config import settings
from app.ingestion.embedder import get_default_embedder
from app.ingestion.extractors import extract
from app.ingestion.jobs import _chunk_segments
from app.retrieval.registry import (
    get_shared_store,
    load_snapshot,
    save_snapshot,
)

DATA = Path("data")
STATE = DATA / "ingest_state.json"
TEST_DATA = Path(__file__).resolve().parents[3] / "test-data"
SNAPSHOT = DATA / "store_snapshot.pkl"


def _video_segments(asset: Path) -> list:
    """Segments for a video, from the transcript disk cache (no network)."""
    from app.ingestion.transcribe import (
        _replay_cached,
        _to_figure_segments,
        _to_segments,
        _to_visual_segments,
        transcript_cache_key,
    )

    if not cache_enabled():
        raise RuntimeError("CACHE_ENABLED=false: cannot re-index a video offline")
    key = transcript_cache_key(asset)
    cached = get_cache("transcripts").get(key)
    if cached is None:
        raise RuntimeError(
            f"no cached transcript for {asset.name} (key {key}); "
            "re-ingest it with LLM_API_KEY set first"
        )
    speech, visual, figures = _replay_cached(cached, None)
    return (
        _to_segments(speech)
        + _to_visual_segments(visual, len(speech))
        + _to_figure_segments(figures, len(speech) + len(visual))
    )


def main() -> None:
    state = json.loads(STATE.read_text(encoding="utf-8"))
    embedder = get_default_embedder()
    expected_dim = settings.expected_embedding_dim()
    print(
        f"active embedder: {embedder.name} "
        f"(expected dim {expected_dim}); "
        f"{'API key' if settings.embedding_api_key else 'no key -> offline'}"
    )

    # Existing snapshot, for the identity check and as the rollback copy.
    old = load_snapshot(SNAPSHOT)
    if old == 0:
        raise RuntimeError(f"no readable snapshot at {SNAPSHOT}; nothing to re-index")
    old_by_id = {c.id: c for c in get_shared_store()._chunks}

    store = get_shared_store()
    total = 0
    for rid, res in state["resources"].items():
        asset = TEST_DATA / (res.get("asset_name") or "")
        if not asset.is_file():
            raise RuntimeError(f"asset missing for resource {rid}: {asset}")
        rtype = str(res["type"])
        segments = (
            _video_segments(asset)
            if rtype == "video"
            else extract(asset, rtype, res.get("youtube_id"))
        )
        row = {
            "id": rid,
            "course_id": res["course_id"],
            "type": rtype,
            "video_id": res.get("youtube_id"),
        }
        chunks = _chunk_segments(segments, row, embedder.name)
        vectors = embedder.embed_batch([c.text for c in chunks]) if chunks else []
        for chunk, vec in zip(chunks, vectors):
            if len(vec) != expected_dim:
                raise RuntimeError(
                    f"embedder {embedder.name} returned {len(vec)} dims, "
                    f"settings expect {expected_dim}"
                )
            chunk.embedding = vec

        bad = [
            c.id
            for c in chunks
            if c.id in old_by_id
            and (
                old_by_id[c.id].text != c.text
                or old_by_id[c.id].start_ms != c.start_ms
                or old_by_id[c.id].end_ms != c.end_ms
            )
        ]
        if bad:
            raise RuntimeError(
                f"re-chunking {rid} changed {len(bad)} chunk(s) vs the existing "
                f"snapshot (e.g. {bad[0]}); refusing to write"
            )

        store.remove_resource(rid)
        store.index(chunks)
        total += len(chunks)
        print(f"  {rid} {rtype:6s} {len(chunks):3d} chunks  {asset.name}")

    dims = {len(c.embedding) for c in store._chunks if c.embedding}
    if dims != {expected_dim}:
        raise RuntimeError(f"post-index dims {dims} != expected {expected_dim}")

    if SNAPSHOT.is_file():
        shutil.copy2(SNAPSHOT, SNAPSHOT.with_suffix(".pkl.bak"))
        print(f"backed up old snapshot -> {SNAPSHOT}.bak")
    saved = save_snapshot(SNAPSHOT)
    print(f"re-indexed {total} chunks; snapshot now holds {saved}, dims={dims}")


if __name__ == "__main__":
    main()