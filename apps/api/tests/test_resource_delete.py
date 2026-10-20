"""DELETE /courses/{course_id}/resources/{resource_id}.

Real Chroma store in ``tmp_path``; uploads redirected to ``tmp_path`` so the
real ``data/`` is never touched.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.subjects import store as subjects_store
from tests.store_helpers import make_chunk, make_embedder

COURSE = "course_1"
OTHER = "course_2"


@pytest.fixture
def env(tmp_path, monkeypatch):
    from app.ingestion import router as ingestion_router
    from app.main import app
    from app.retrieval import registry
    from app.retrieval.store import ChromaVectorStore

    monkeypatch.setattr(subjects_store, "SUBJECT_STATE_PATH", tmp_path / "s.json")
    subjects_store.set_persistence_enabled(False)
    subjects_store.reset_store()

    uploads = tmp_path / "uploads"
    uploads.mkdir()
    monkeypatch.setattr(ingestion_router, "UPLOAD_DIR", uploads)

    store = ChromaVectorStore(
        path=tmp_path / "chroma",
        embedder=make_embedder(),
        collection="test_delete_chunks",
    )
    store.add(
        [
            make_chunk("ch_alpha", resource_id="res_alpha", course_id=COURSE),
            make_chunk("ch_alpha2", resource_id="res_alpha2", course_id=COURSE),
            make_chunk("ch_other", resource_id="res_other", course_id=OTHER),
        ]
    )
    for rid in ("res_alpha", "res_alpha2", "res_other"):
        (uploads / f"{rid}.mp4").write_bytes(b"bytes")

    previous = registry.get_shared_store()
    registry.set_shared_store(store)
    try:
        with TestClient(app) as client:
            yield client, store, uploads
    finally:
        registry.set_shared_store(previous)
        subjects_store.set_persistence_enabled(False)
        subjects_store.reset_store()


def _ids(store, course_id):
    return {r["id"] for r in store.resources(course_id)}


def _subject(client, name, rids):
    sub = client.post("/subjects", json={"name": name, "course_id": COURSE}).json()
    for rid in rids:
        r = client.post(f"/subjects/{sub['id']}/resources", json={"resource_id": rid})
        assert r.status_code < 300, r.text
    return sub["id"]


def test_delete_removes_chunks_and_asset(env):
    client, store, uploads = env
    r = client.delete(f"/courses/{COURSE}/resources/res_alpha")
    assert r.status_code == 200, r.text
    assert r.json() == {
        "resource_id": "res_alpha",
        "deleted": True,
        "detached_from": [],
        "asset_removed": True,
    }
    assert "res_alpha" not in _ids(store, COURSE)
    assert not (uploads / "res_alpha.mp4").exists()


def test_detaches_from_every_subject(env):
    client, _, _ = env
    s1 = _subject(client, "A", ["res_alpha", "res_alpha2"])
    s2 = _subject(client, "B", ["res_alpha"])
    s3 = _subject(client, "C", ["res_alpha2"])
    r = client.delete(f"/courses/{COURSE}/resources/res_alpha")
    assert r.status_code == 200
    assert sorted(r.json()["detached_from"]) == sorted([s1, s2])
    assert subjects_store.subject_store.resource_ids(s1) == ["res_alpha2"]
    assert subjects_store.subject_store.resource_ids(s2) == []
    assert subjects_store.subject_store.resource_ids(s3) == ["res_alpha2"]


def test_wrong_course_is_404_and_untouched(env):
    client, store, uploads = env
    sid = _subject(client, "A", ["res_alpha"])
    r = client.delete(f"/courses/{OTHER}/resources/res_alpha")
    assert r.status_code == 404
    assert "res_alpha" in _ids(store, COURSE)
    assert (uploads / "res_alpha.mp4").exists()
    assert subjects_store.subject_store.resource_ids(sid) == ["res_alpha"]


def test_other_resources_untouched_exact_id(env):
    client, store, uploads = env
    assert client.delete(f"/courses/{COURSE}/resources/res_alpha").status_code == 200
    assert _ids(store, COURSE) == {"res_alpha2"}
    assert _ids(store, OTHER) == {"res_other"}
    assert (uploads / "res_alpha2.mp4").exists()
    assert (uploads / "res_other.mp4").exists()


def test_second_delete_is_404(env):
    client, _, _ = env
    assert client.delete(f"/courses/{COURSE}/resources/res_alpha").status_code == 200
    assert client.delete(f"/courses/{COURSE}/resources/res_alpha").status_code == 404


@pytest.mark.parametrize(
    "bad", ["..%2Fres_alpha", "res_alpha.mp4", "..", "res_alpha%2F..%2Fx", "res%5Calpha"]
)
def test_traversal_shaped_id_rejected(env, bad):
    client, store, uploads = env
    r = client.delete(f"/courses/{COURSE}/resources/{bad}")
    assert r.status_code in (404, 405, 422)
    assert _ids(store, COURSE) == {"res_alpha", "res_alpha2"}
    assert len(list(uploads.iterdir())) == 3


def test_media_404_after_delete(env):
    client, _, _ = env
    assert client.get("/resources/res_alpha/media").status_code == 200
    assert client.delete(f"/courses/{COURSE}/resources/res_alpha").status_code == 200
    assert client.get("/resources/res_alpha/media").status_code == 404
    assert client.get("/resources/res_alpha2/media").status_code == 200
