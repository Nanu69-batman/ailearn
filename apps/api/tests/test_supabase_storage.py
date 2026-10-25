# ruff: noqa: F811
"""Uploads mirrored to Supabase Storage + the ``resources`` table. No network."""

from __future__ import annotations

import json
import time

import httpx
import jwt
import pytest

from app.config import settings
from app.ingestion import jobs as jobs_mod
from app.ingestion import remote_assets
from app.ingestion import router as router_mod
from app.supa.client import SupabaseClient, set_client
from tests.store_helpers import make_chunk
from tests.test_ingestion import client, wired  # noqa: F401  (fixtures)

SECRET = "test-jwt-secret-at-least-32-bytes-long!!"
OWNER = "8b1f6c1e-0000-4000-8000-000000000001"
OTHER = "8b1f6c1e-0000-4000-8000-000000000002"
BYTES = b"\x00" * 32


def _token(sub: str) -> dict:
    now = int(time.time())
    tok = jwt.encode(
        {"sub": sub, "aud": "authenticated", "exp": now + 3600, "iat": now},
        SECRET, algorithm="HS256",
    )
    return {"Authorization": f"Bearer {tok}"}


class Fake:
    """Records requests; serves one stored row / object."""

    def __init__(self, *, fail: bool = False, row: dict | None = None, blob: bytes = b""):
        self.calls: list[tuple[str, str, bytes]] = []
        self.fail = fail
        self.row = row
        self.blob = blob

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append((req.method, req.url.path, req.content))
        if self.fail:
            return httpx.Response(500, text="boom")
        path = req.url.path
        if req.method == "GET" and path.startswith("/rest/v1/resources"):
            return httpx.Response(200, json=[self.row] if self.row else [])
        if req.method == "GET" and path.startswith("/storage/v1/object/"):
            return httpx.Response(200, content=self.blob)
        return httpx.Response(200, json={})

    def paths(self, method: str) -> list[str]:
        return [p for m, p, _ in self.calls if m == method]


def _install(fake: Fake) -> None:
    set_client(
        SupabaseClient("http://sb.test", "svc", transport=httpx.MockTransport(fake))
    )


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(settings, "supabase_jwt_secret", SECRET)
    monkeypatch.setattr(settings, "auth_required", False)
    monkeypatch.setattr(settings, "supabase_storage_bucket", "media")
    monkeypatch.setattr(
        jobs_mod, "ingest_media",
        lambda path, **kw: {"total_chunks": 1, "audio_chunks": 1, "visual_chunks": 0,
                            "duration_ms": 1, "max_end_ms": 1},
    )
    yield
    remote_assets.wait_idle()
    set_client(None)


def _upload(client, headers=None):
    resp = client.post(
        "/courses/course_1/ingest",
        files={"file": ("lecture.mp4", BYTES, "video/mp4")},
        headers=headers or {},
    )
    assert resp.status_code == 200, resp.text
    remote_assets.wait_idle()
    return resp.json()["resource_id"]


def test_upload_is_mirrored_and_row_upserted(client):
    fake = Fake()
    _install(fake)
    rid = _upload(client, _token(OWNER))

    uploads = [p for p in fake.paths("POST") if p.startswith("/storage/v1/object/media/")]
    assert len(uploads) == 1
    assert f"{OWNER}/{rid}/{rid}.mp4" in uploads[0].replace("%2F", "/")
    rows = [json.loads(b) for m, p, b in fake.calls if p == "/rest/v1/resources"]
    assert len(rows) == 1
    row = rows[0][0]
    assert row["id"] == rid and row["owner_id"] == OWNER
    assert row["course_id"] == "course_1" and row["source_type"] == "video"
    assert row["storage_path"] == f"{OWNER}/{rid}/{rid}.mp4"
    assert row["size_bytes"] == len(BYTES) and row["mime"] == "video/mp4"


def test_anonymous_upload_uses_public_prefix_and_null_owner(client):
    fake = Fake()
    _install(fake)
    rid = _upload(client)
    row = next(json.loads(b)[0] for m, p, b in fake.calls if p == "/rest/v1/resources")
    assert row["owner_id"] is None
    assert row["storage_path"] == f"public/{rid}/{rid}.mp4"


def test_supabase_failure_does_not_break_ingest(client):
    _install(Fake(fail=True))
    rid = _upload(client)
    assert router_mod.resolve_asset(rid) is not None


def test_missing_local_file_is_restored_from_storage(client):
    rid = "res_abc123def456"
    fake = Fake(
        row={"id": rid, "owner_id": None, "storage_path": f"public/{rid}/{rid}.mp4"},
        blob=b"restored-bytes",
    )
    _install(fake)
    assert router_mod.resolve_asset(rid) is None
    resp = client.get(f"/resources/{rid}/media")
    assert resp.status_code == 200 and resp.content == b"restored-bytes"
    assert router_mod.resolve_asset(rid).name == f"{rid}.mp4"


def test_missing_file_without_row_is_404(client):
    _install(Fake())
    assert client.get("/resources/res_nope/media").status_code == 404


def test_purge_removes_object_and_row(wired, tmp_path):
    from app.ingestion.purge import purge_resource

    rid = "res_purge1"
    fake = Fake(row={"id": rid, "owner_id": None, "storage_path": f"public/{rid}/{rid}.mp4"})
    _install(fake)
    purge_resource(rid, store=wired, upload_dir=tmp_path)
    assert fake.paths("DELETE") == ["/storage/v1/object/media", "/rest/v1/resources"]


def test_delete_by_non_owner_is_404_and_owner_succeeds(client, wired):
    rid = "res_owned1"
    wired.add([make_chunk("c1", resource_id=rid, course_id="course_1")])
    fake = Fake(row={"id": rid, "owner_id": OWNER, "storage_path": None})
    _install(fake)

    url = f"/courses/course_1/resources/{rid}"
    assert client.delete(url, headers=_token(OTHER)).status_code == 404
    assert client.delete(url).status_code == 404
    assert fake.paths("DELETE") == []

    assert client.delete(url, headers=_token(OWNER)).status_code == 200
    assert "/rest/v1/resources" in fake.paths("DELETE")


def test_supabase_off_makes_no_calls(client, monkeypatch):
    set_client(None)
    monkeypatch.setattr(settings, "supabase_url", "")
    monkeypatch.setattr(settings, "supabase_service_role_key", "")
    called = []
    monkeypatch.setattr(
        remote_assets, "_spawn", lambda *a, **k: called.append(a), raising=True
    )
    rid = _upload(client)
    assert router_mod.resolve_asset(rid) is not None
    assert called == []
    assert client.get("/resources/res_none/media").status_code == 404
