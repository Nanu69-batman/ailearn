"""Subject organisation API and the retrieval scope contract.

The tests that matter most here are the two about ``None`` vs ``[]``:
``resolve_subject_resource_scope`` must distinguish "unscoped / subject does
not exist" from "scoped to an empty set / subject has no materials". Swapping
them is invisible in a green suite — one silently widens retrieval to the whole
corpus, the other silently black-holes it — so both are pinned explicitly.

The store fixture is a *real* Chroma store in ``tmp_path``, not a fake:
resource existence is what makes a scope valid, so testing it against a
double would only prove the double agrees with itself. Persistence is
monkeypatched at a ``tmp_path`` and never touches the real ``data/``.
"""

from __future__ import annotations

import threading

import pytest
from fastapi.testclient import TestClient

from app.subjects import store as subjects_store
from app.subjects.store import resolve_subject_resource_scope
from tests.store_helpers import make_chunk, make_embedder

COURSE = "course_1"


@pytest.fixture
def subject_store(tmp_path, monkeypatch):
    """A subject store backed by a real Chroma registry in ``tmp_path``.

    Seeds ``KNOWN_RESOURCE_IDS`` as indexed chunks of ``COURSE`` so attach can
    be validated against real metadata rather than a stub.
    """
    from app.retrieval import registry
    from app.retrieval.store import ChromaVectorStore

    monkeypatch.setattr(
        subjects_store,
        "SUBJECT_STATE_PATH",
        tmp_path / "subjects.json",
    )
    subjects_store.set_persistence_enabled(False)
    subjects_store.reset_store()

    known = ["res_alpha", "res_beta", "res_gamma"]
    store = ChromaVectorStore(
        path=tmp_path / "chroma",
        embedder=make_embedder(),
        collection="test_subject_chunks",
    )
    store.add(
        [
            make_chunk(f"ch_{rid}", resource_id=rid, course_id=COURSE)
            for rid in known
        ]
    )

    previous = registry.get_shared_store()
    registry.set_shared_store(store)
    try:
        yield store, known
    finally:
        registry.set_shared_store(previous)
        subjects_store.set_persistence_enabled(False)
        subjects_store.reset_store()


@pytest.fixture
def client(subject_store):
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def _create(client, name="Physics 101", **kwargs) -> dict:
    response = client.post("/subjects", json={"name": name, **kwargs})
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def test_create_and_list_subjects(client, subject_store):
    created = _create(client, "Physics 101", description="mechanics")
    assert created["name"] == "Physics 101"
    assert created["description"] == "mechanics"
    assert created["course_id"] is None
    assert created["resource_ids"] == []
    assert created["resource_count"] == 0
    assert created["created_at"]

    _create(client, "Chemistry 101")

    listing = client.get("/subjects").json()
    assert len(listing["subjects"]) == 2
    assert {s["name"] for s in listing["subjects"]} == {"Physics 101", "Chemistry 101"}

    fetched = client.get(f"/subjects/{created['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == created["id"]

    assert client.get("/subjects/sub_missing").status_code == 404

    patched = client.patch(
        f"/subjects/{created['id']}", json={"name": "Physics II", "description": "n/a"}
    )
    assert patched.status_code == 200
    assert patched.json()["name"] == "Physics II"
    assert patched.json()["description"] == "n/a"

    deleted = client.delete(f"/subjects/{created['id']}")
    assert deleted.status_code == 200
    assert client.get(f"/subjects/{created['id']}").status_code == 404
    assert client.delete(f"/subjects/{created['id']}").status_code == 404


def test_create_rejects_unknown_fields(client, subject_store):
    assert client.post("/subjects", json={"name": "x", "bogus": 1}).status_code == 422
    assert client.post("/subjects", json={"name": ""}).status_code == 422


# ---------------------------------------------------------------------------
# Membership
# ---------------------------------------------------------------------------


def test_attach_and_detach_resource(client, subject_store):
    _, known = subject_store
    subject = _create(client)

    attached = client.post(
        f"/subjects/{subject['id']}/resources", json={"resource_id": known[0]}
    )
    assert attached.status_code == 200
    assert attached.json()["resource_ids"] == [known[0]]
    assert attached.json()["resource_count"] == 1

    # Re-attaching the same id is idempotent, not a duplicate.
    again = client.post(
        f"/subjects/{subject['id']}/resources", json={"resource_id": known[0]}
    )
    assert again.json()["resource_ids"] == [known[0]]

    detached = client.delete(f"/subjects/{subject['id']}/resources/{known[0]}")
    assert detached.status_code == 200
    assert detached.json()["resource_ids"] == []

    assert (
        client.delete(f"/subjects/sub_missing/resources/{known[0]}").status_code == 404
    )


def test_attach_unknown_resource_is_rejected(client, subject_store):
    subject = _create(client)

    response = client.post(
        f"/subjects/{subject['id']}/resources", json={"resource_id": "res_does_not_exist"}
    )
    assert response.status_code == 422
    assert "res_does_not_exist" in response.json()["detail"]

    # Nothing was attached: the failed reference must not linger in scope.
    assert client.get(f"/subjects/{subject['id']}").json()["resource_ids"] == []

    # A missing subject is 404, not 422 — the two failures stay distinct.
    assert (
        client.post(
            "/subjects/sub_missing/resources", json={"resource_id": "res_alpha"}
        ).status_code
        == 404
    )


# ---------------------------------------------------------------------------
# Retrieval scope contract
# ---------------------------------------------------------------------------


def test_subject_scope_returns_only_its_resources(client, subject_store):
    _, known = subject_store
    mine = _create(client, "Mine")
    theirs = _create(client, "Theirs")

    for rid in known[:2]:
        client.post(f"/subjects/{mine['id']}/resources", json={"resource_id": rid})
    client.post(
        f"/subjects/{theirs['id']}/resources", json={"resource_id": known[2]}
    )

    assert resolve_subject_resource_scope(mine["id"]) == known[:2]
    assert resolve_subject_resource_scope(theirs["id"]) == [known[2]]

    # The other subject's material is unreachable from this scope.
    assert known[2] not in resolve_subject_resource_scope(mine["id"])


def test_empty_subject_returns_empty_list_not_none(client, subject_store):
    subject = _create(client)

    scope = resolve_subject_resource_scope(subject["id"])

    assert scope == []
    # Explicitly not None: an empty subject means "nothing retrievable", which
    # must never be confused with "no scope requested".
    assert scope is not None


def test_unknown_subject_returns_none(client, subject_store):
    assert resolve_subject_resource_scope("sub_missing") is None


# ---------------------------------------------------------------------------
# Persistence + concurrency
# ---------------------------------------------------------------------------


def test_subjects_survive_reload(client, subject_store, tmp_path, monkeypatch):
    _, known = subject_store
    subject = _create(client, "Persisted", description="kept", course_id=COURSE)
    client.post(
        f"/subjects/{subject['id']}/resources", json={"resource_id": known[0]}
    )

    # Persist to the tmp_path the fixture monkeypatched in.
    subjects_store.set_persistence_enabled(True)
    subjects_store.save_subject_state()
    assert subjects_store.SUBJECT_STATE_PATH.is_file()

    # Simulate a restart: memory dropped, file reloaded.
    subjects_store.reset_store()
    assert subjects_store.subject_store.get(subject["id"]) is None
    assert subjects_store.load_subject_state() == 1

    restored = subjects_store.subject_store.get(subject["id"])
    assert restored is not None
    assert restored.name == "Persisted"
    assert restored.description == "kept"
    assert restored.course_id == COURSE
    assert restored.resource_ids == [known[0]]
    assert resolve_subject_resource_scope(subject["id"]) == [known[0]]

    # Malformed rows are skipped, not fatal.
    subjects_store.SUBJECT_STATE_PATH.write_text(
        '{"subjects": {"sub_ok": {"id": "sub_ok", "name": "ok"}, "sub_bad": 7}}',
        encoding="utf-8",
    )
    subjects_store.reset_store()
    assert subjects_store.load_subject_state() == 1
    assert resolve_subject_resource_scope("sub_bad") is None


def test_concurrent_attach_does_not_lose_resources(client, subject_store):
    _, known = subject_store
    subject = _create(client)

    errors: list[BaseException] = []
    barrier = threading.Barrier(len(known))

    def attach(resource_id: str) -> None:
        try:
            barrier.wait(timeout=10)
            client.post(
                f"/subjects/{subject['id']}/resources",
                json={"resource_id": resource_id},
            )
        except BaseException as exc:  # noqa: BLE001 - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=attach, args=(rid,)) for rid in known]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors
    # Read-modify-write race: a snapshot-then-write without the lock drops
    # whichever attach serialized last.
    assert sorted(client.get(f"/subjects/{subject['id']}").json()["resource_ids"]) == sorted(
        known
    )
# ---------------------------------------------------------------------------
# GET /subjects/{id}/resources  (the subject-scoped catalogue)
# ---------------------------------------------------------------------------


def test_subject_resources_returns_attached_resources(client, subject_store):
    """A subject lists its own materials, in attachment order.

    The bug this pins: the UI called ``/courses/{subject_id}/resources``, whose
    empty result made a populated subject render "no materials". Two subjects
    on the SAME course must still differ, so the subject's own attachment list
    is the only valid source.
    """
    _, known = subject_store
    mine = _create(client, "Mine", course_id=COURSE)
    theirs = _create(client, "Theirs", course_id=COURSE)

    for rid in (known[0], known[1]):
        client.post(f"/subjects/{mine['id']}/resources", json={"resource_id": rid})
    client.post(f"/subjects/{theirs['id']}/resources", json={"resource_id": known[2]})

    response = client.get(f"/subjects/{mine['id']}/resources")

    assert response.status_code == 200, response.text
    payload = response.json()
    # Same envelope as GET /courses/{id}/resources.
    assert set(payload) == {"resources"}
    ids = [entry["id"] for entry in payload["resources"]]
    # Attachment order is preserved, not the registry's arbitrary order.
    assert ids == known[:2]
    # Shape parity with the course listing: titles render without a 2nd lookup.
    for entry in payload["resources"]:
        assert set(entry) >= {
            "id",
            "title",
            "type",
            "provider",
            "status",
            "youtube_id",
        }

    # Two subjects on one course do NOT collapse into the same list.
    theirs_ids = [
        entry["id"] for entry in client.get(f"/subjects/{theirs['id']}/resources").json()["resources"]
    ]
    assert theirs_ids == [known[2]]
    assert ids != theirs_ids


def test_subject_resources_unknown_subject_404s(client, subject_store):
    assert client.get("/subjects/sub_missing/resources").status_code == 404


def test_subject_resources_empty_returns_empty_list(client, subject_store):
    """Known-but-empty is 200 + [], distinct from the 404 above."""
    subject = _create(client)

    response = client.get(f"/subjects/{subject['id']}/resources")

    assert response.status_code == 200
    assert response.json() == {"resources": []}


def test_subject_resources_skips_vanished_resource_ids(client, subject_store):
    """A dangling id degrades to a shorter list instead of a 500."""
    _, known = subject_store
    subject = _create(client)
    for rid in (known[0], known[2]):
        client.post(f"/subjects/{subject['id']}/resources", json={"resource_id": rid})

    # Delete the underlying resource behind the subject's back, as a re-index
    # or an out-of-band cleanup would.
    from app.retrieval import registry

    registry.get_shared_store().delete_resource(known[0])

    response = client.get(f"/subjects/{subject['id']}/resources")

    assert response.status_code == 200, response.text
    ids = [entry["id"] for entry in response.json()["resources"]]
    assert ids == [known[2]]


# ---------------------------------------------------------------------------
# asset_name parity between subject and course listings
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_uploads(tmp_path, monkeypatch):
    """Redirect the upload dir so tests never touch real ``data/``."""
    from app.ingestion import router as ingestion_router

    uploads = tmp_path / "uploads"
    uploads.mkdir()
    monkeypatch.setattr(ingestion_router, "UPLOAD_DIR", uploads)
    return uploads


def test_subject_resource_entry_preserves_asset_name(
    client, subject_store, tmp_uploads
):
    _, known = subject_store
    (tmp_uploads / f"{known[0]}.mp4").write_bytes(b"fake-mp4")
    subject = _create(client, "Mine", course_id=COURSE)
    client.post(
        f"/subjects/{subject['id']}/resources", json={"resource_id": known[0]}
    )

    subject_entry = client.get(f"/subjects/{subject['id']}/resources").json()[
        "resources"
    ][0]
    course_entries = client.get(f"/courses/{COURSE}/resources").json()["resources"]
    course_entry = next(e for e in course_entries if e["id"] == known[0])

    assert subject_entry["asset_name"] == course_entry["asset_name"]
    assert subject_entry["asset_name"] == f"{known[0]}.mp4"


def test_subject_resource_entry_matches_course_shape(
    client, subject_store, tmp_uploads
):
    _, known = subject_store
    (tmp_uploads / f"{known[0]}.mp4").write_bytes(b"fake-mp4")
    subject = _create(client, "Mine", course_id=COURSE)
    client.post(
        f"/subjects/{subject['id']}/resources", json={"resource_id": known[0]}
    )

    subject_entry = client.get(f"/subjects/{subject['id']}/resources").json()[
        "resources"
    ][0]
    course_entry = next(
        e
        for e in client.get(f"/courses/{COURSE}/resources").json()["resources"]
        if e["id"] == known[0]
    )

    assert subject_entry == course_entry


def test_metadata_fallback_does_not_null_asset_name(
    client, subject_store, tmp_uploads
):
    """When the registry's ``resources()`` path yields nothing (course-less
    subject), metadata-derived entries must still surface the real
    ``asset_name`` from the upload dir — never a bare null, because the
    web client hides any file video without one.
    """
    _, known = subject_store
    (tmp_uploads / f"{known[0]}.mp4").write_bytes(b"fake-mp4")
    subject = _create(client, "No course link")  # course_id=None
    client.post(
        f"/subjects/{subject['id']}/resources", json={"resource_id": known[0]}
    )

    response = client.get(f"/subjects/{subject['id']}/resources")

    assert response.status_code == 200, response.text
    entry = response.json()["resources"][0]
    assert entry["asset_name"] == f"{known[0]}.mp4"
