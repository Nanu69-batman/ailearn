"""A resource purge must leave nothing behind that can still serve bytes.

Every test here is rooted in ``tmp_path``: the uploads directory is
monkeypatched and the Chroma store is the ``chroma_store`` fixture, so no test
can read or write the developer's ``data/`` -- in particular nothing here comes
near the live ``res_2e629d682a3d.mp4`` demo asset.
"""

from __future__ import annotations

import pytest

from app.ingestion import router as ingestion_router
from app.ingestion.purge import purge_resource
from tests.store_helpers import make_chunk


@pytest.fixture
def uploads(tmp_path, monkeypatch):
    """A throwaway uploads directory standing in for the real one."""
    directory = tmp_path / "uploads"
    directory.mkdir()
    monkeypatch.setattr(ingestion_router, "UPLOAD_DIR", directory)
    return directory


def _stage(uploads, resource_id: str, body: bytes = b"fake-mp4-bytes") -> object:
    asset = uploads / f"{resource_id}.mp4"
    asset.write_bytes(body)
    return asset


def _index(chroma_store, resource_id: str, n: int = 3) -> None:
    chroma_store.add(
        [
            make_chunk(f"{resource_id}_c{i}", resource_id=resource_id)
            for i in range(n)
        ]
    )


def test_purge_removes_chunks_and_asset(chroma_store, uploads):
    """The whole point: both halves of the resource go, not just the chunks."""
    _index(chroma_store, "res_purge_me")
    asset = _stage(uploads, "res_purge_me")
    assert chroma_store.count() == 3
    assert asset.exists()

    report = purge_resource("res_purge_me", store=chroma_store)

    assert chroma_store.count() == 0
    assert not asset.exists()
    assert report["asset_removed"] is True
    assert report["asset_name"] == "res_purge_me.mp4"


def test_purge_is_idempotent(chroma_store, uploads):
    """Purging twice is a no-op, not an error -- operators retry, and cleanup
    scripts double-run."""
    _index(chroma_store, "res_twice")
    asset = _stage(uploads, "res_twice")

    first = purge_resource("res_twice", store=chroma_store)
    second = purge_resource("res_twice", store=chroma_store)

    assert first["asset_removed"] is True
    assert second["asset_removed"] is False
    assert not asset.exists()
    assert chroma_store.count() == 0


def test_purge_with_no_asset_still_removes_chunks(chroma_store, uploads):
    """Absence of a file is not an error: documents may never have had one."""
    _index(chroma_store, "res_no_file")
    assert list(uploads.iterdir()) == []

    report = purge_resource("res_no_file", store=chroma_store)

    assert chroma_store.count() == 0
    assert report["asset_name"] is None
    assert report["asset_removed"] is False


def test_purge_never_touches_another_resources_asset(chroma_store, uploads):
    """Two staged files, one purged: the other survives, chunks included."""
    _index(chroma_store, "res_keep", n=2)
    doomed = _stage(uploads, "res_drop")
    survivor = _stage(uploads, "res_keep", b"other-video")
    _index(chroma_store, "res_drop")

    purge_resource("res_drop", store=chroma_store)

    assert not doomed.exists()
    assert survivor.exists()
    assert survivor.read_bytes() == b"other-video"
    # The other resource's chunks are untouched by the delete predicate.
    assert chroma_store.count() == 2


def test_purge_refuses_a_path_outside_the_uploads_directory(chroma_store, uploads):
    """A traversal-shaped id resolves to no asset, so nothing outside the
    uploads directory can be deleted even though a decoy sits there."""
    outside = uploads.parent / "outside.mp4"
    outside.write_bytes(b"must survive")
    _index(chroma_store, "res_victim")

    report = purge_resource("../../outside", store=chroma_store)

    assert outside.exists()
    assert outside.read_bytes() == b"must survive"
    assert report["asset_name"] is None
    assert report["asset_removed"] is False


def test_purge_does_not_sweep_unrelated_files(chroma_store, uploads):
    """No directory sweep, no prefix match: unrelated files are untouchable."""
    stray_txt = uploads / "notes.txt"
    stray_txt.write_text("not a staged asset")
    other_asset = uploads / "res_somethingelse.mp4"
    other_asset.write_bytes(b"bytes")

    purge_resource("res_absent", store=chroma_store)

    assert stray_txt.exists()
    assert other_asset.exists()