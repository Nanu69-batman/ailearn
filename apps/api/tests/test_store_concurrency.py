"""Concurrency regression tests for the in-memory store + snapshot persistence.

Defect: ingestion mutates ``InMemoryVectorStore`` from a background thread
while request threads search it and ``save_snapshot`` serialises it. Two
distinct failures resulted:

1. ``index()`` was a non-atomic read-modify-write -- it rebound ``_chunks``
   and *then* extended the rebound list -- so two concurrent ingests could
   extend an orphaned list and silently lose each other's chunks.
2. ``save_snapshot()`` walked ``store._chunks`` directly while it was being
   rebuilt, and wrote the pickle in place, so a reader could observe a
   half-written ``data/store_snapshot.pkl`` (a truncated pickle, i.e. every
   already-paid-for embedding lost on restart).

Both are fixed by a ``threading.Lock`` in the store plus an atomic
temp-file + ``os.replace`` write. These tests use threads only -- no network,
no embedder, no Gemini.
"""

from __future__ import annotations

import asyncio
import pickle
import threading

import pytest

from app.ingestion.models import Chunk, text_hash
from app.retrieval import registry as registry_mod
from app.retrieval.store import InMemoryVectorStore


def make_chunk(cid: str, resource_id: str, text: str) -> Chunk:
    return Chunk(
        id=cid,
        resource_id=resource_id,
        course_id="course1",
        source_type="pdf",
        chunk_index=0,
        text=text,
        text_hash=text_hash(text),
        embed_model="test-embedder",
        permitted=True,
        embedding=[0.1, 0.2, 0.3],
    )


def test_snapshot_chunks_is_a_copy_not_the_live_list():
    """The snapshot primitive must not hand out the mutable internal list."""
    store = InMemoryVectorStore([make_chunk("a", "r1", "alpha")])
    snap = store.snapshot_chunks()
    snap.append(make_chunk("b", "r1", "beta"))
    assert len(store.snapshot_chunks()) == 1


def test_concurrent_index_and_snapshot_save(tmp_path, monkeypatch):
    """Concurrent index/remove/search vs save_snapshot: no crash, no lost file."""
    monkeypatch.setattr(registry_mod, "SNAPSHOT_PATH", tmp_path / "snap.pkl")
    store = registry_mod.reset_shared_store()
    registry_mod._shared_store = store

    errors: list[BaseException] = []
    stop = threading.Event()
    # 4 distinct resources => concurrent index() calls touch disjoint ids but
    # all read-modify-write the same list, which is the race under test.
    resources = [f"r{i}" for i in range(4)]

    def guard(fn):
        def run() -> None:
            try:
                fn()
            except BaseException as exc:  # noqa: BLE001 - recorded and re-raised
                errors.append(exc)
                stop.set()

        return run

    def indexer(resource_id: str):
        def run() -> None:
            i = 0
            while not stop.is_set():
                store.index([make_chunk(f"{resource_id}_c{i}", resource_id, f"t{i}")])
                i += 1

        return run

    def remover(resource_id: str):
        def run() -> None:
            while not stop.is_set():
                store.remove_resource(resource_id)

        return run

    def searcher():
        async def once() -> None:
            await store.search("course1", resources, 60_000, [0.1, 0.2, 0.3], k=5)

        while not stop.is_set():
            asyncio.run(once())

    def snapshotter():
        while not stop.is_set():
            n = registry_mod.save_snapshot()
            assert n >= 0

    threads = [threading.Thread(target=guard(indexer(r))) for r in resources]
    threads += [threading.Thread(target=guard(remover(r))) for r in resources]
    threads += [
        threading.Thread(target=guard(searcher)),
        threading.Thread(target=guard(snapshotter)),
        threading.Thread(target=guard(snapshotter)),
    ]
    for t in threads:
        t.daemon = True
        t.start()
    # Let the threads interleave for a bounded time, then stop them.
    threading.Event().wait(2.0)
    stop.set()
    for t in threads:
        t.join(timeout=5)
        assert not t.is_alive(), "thread did not stop"

    assert not errors, f"concurrent access raised: {errors[0]!r}"

    # The snapshot on disk must be a complete, loadable pickle -- never a
    # truncated one. os.replace guarantees readers never see a partial write.
    target = tmp_path / "snap.pkl"
    assert target.is_file()
    with open(target, "rb") as fh:
        restored = pickle.load(fh)
    assert isinstance(restored, list)
    # And no staging file was left behind.
    assert list(tmp_path.glob("*.tmp")) == []


def test_concurrent_index_does_not_lose_chunks():
    """Two threads indexing disjoint resources must both survive.

    Pre-fix, ``index`` rebound ``_chunks`` and then extended the rebound list;
    thread B's rebind between those two steps left thread A's chunks on an
    orphaned list, so the store silently dropped half its data.
    """
    store = InMemoryVectorStore([])
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def indexer(resource_id: str):
        def run() -> None:
            try:
                barrier.wait(timeout=5)
                store.index([make_chunk(f"{resource_id}_c", resource_id, resource_id)])
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        return run

    threads = [
        threading.Thread(target=indexer("res_a")),
        threading.Thread(target=indexer("res_b")),
    ]
    for t in threads:
        t.daemon = True
        t.start()
    for t in threads:
        t.join(timeout=5)
    assert not errors, f"indexing raised: {errors[0]!r}"
    ids = {c.id for c in store.snapshot_chunks()}
    assert {"res_a_c", "res_b_c"} <= ids


def test_search_observes_a_consistent_snapshot():
    """A search running during heavy mutation must never raise or tear."""
    store = InMemoryVectorStore([make_chunk("seed", "res_a", "seed")])
    errors: list[BaseException] = []
    stop = threading.Event()

    def mutator() -> None:
        i = 0
        while not stop.is_set():
            store.index([make_chunk(f"m{i}", "res_a", f"m{i}")])
            i += 1

    def searcher() -> None:
        async def once() -> None:
            hits = await store.search(
                "course1", ["res_a"], 60_000, [0.1, 0.2, 0.3], k=3
            )
            assert isinstance(hits, list)

        while not stop.is_set():
            try:
                asyncio.run(once())
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)
                stop.set()

    t1 = threading.Thread(target=mutator, daemon=True)
    t2 = threading.Thread(target=searcher, daemon=True)
    t1.start()
    t2.start()
    threading.Event().wait(1.5)
    stop.set()
    t1.join(timeout=5)
    t2.join(timeout=5)
    assert not errors, f"concurrent search raised: {errors[0]!r}"


@pytest.mark.parametrize("path_arg", ["explicit"])
def test_save_snapshot_is_atomic_via_replace(tmp_path, monkeypatch, path_arg):
    """The write must land via os.replace, leaving the target never partial."""
    monkeypatch.setattr(registry_mod, "SNAPSHOT_PATH", tmp_path / "snap.pkl")
    registry_mod.reset_shared_store().index([make_chunk("a", "r1", "alpha")])

    before = {p.name for p in tmp_path.iterdir()}
    assert registry_mod.save_snapshot() == 1

    after = {p.name for p in tmp_path.iterdir()}
    # Exactly one new file: the target itself. No ``.tmp`` residue.
    assert after - before == {"snap.pkl"}
    with open(tmp_path / "snap.pkl", "rb") as fh:
        assert [c.id for c in pickle.load(fh)] == ["a"]