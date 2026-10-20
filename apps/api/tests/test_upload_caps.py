# ruff: noqa: F811
"""Free-tier caps on uploads for a public deployment. Fakes only."""

from __future__ import annotations

import pytest

import app.ingestion.router as router_mod
from app.config import settings
from tests.test_ingestion import client, wired  # noqa: F401  (fixtures)


@pytest.fixture(autouse=True)
def _no_real_work(monkeypatch):
    monkeypatch.setattr(router_mod, "schedule", lambda job, work: None)
    router_mod.reset_upload_slots()
    yield
    router_mod.reset_upload_slots()


def _upload(client, name="lecture.mp4", body=b"\x00" * 32, mime="video/mp4"):
    return client.post("/courses/course_1/ingest", files={"file": (name, body, mime)})


def test_caps_off_when_zero(client, monkeypatch):
    for name in ("max_upload_mb", "max_video_minutes", "max_uploads_per_hour"):
        monkeypatch.setattr(settings, name, 0)
    assert _upload(client).status_code == 200


def test_upload_size_cap(client, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "max_upload_mb", 1)
    r = _upload(client, body=b"\x00" * (1024 * 1024 + 1))
    assert r.status_code == 413 and "1MB" in r.json()["detail"]
    assert not list((tmp_path / "uploads").glob("*"))  # nothing left staged


def test_video_length_cap(client, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "max_video_minutes", 15)
    monkeypatch.setattr(router_mod, "media_duration_ms", lambda p: 20 * 60_000)
    r = _upload(client)
    assert r.status_code == 413 and "15 min" in r.json()["detail"]
    assert not list((tmp_path / "uploads").glob("*"))

    monkeypatch.setattr(router_mod, "media_duration_ms", lambda p: 10 * 60_000)
    assert _upload(client).status_code == 200


def test_uploads_per_hour_cap(client, monkeypatch):
    monkeypatch.setattr(settings, "max_uploads_per_hour", 2)
    assert _upload(client).status_code == 200
    assert _upload(client).status_code == 200
    r = _upload(client)
    assert r.status_code == 429 and "2 uploads per hour" in r.json()["detail"]
