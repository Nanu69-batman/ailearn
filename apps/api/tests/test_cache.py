"""Offline tests for the billable-call disk caches (no network, no keys)."""

import json

import app.cache as cache_module
from app.cache import DiskCache, reset_caches, sha1hex


def test_sha1hex_stable_and_sensitive(tmp_path):
    assert sha1hex("a", "b") == sha1hex("a", "b")
    assert sha1hex("a", "b") != sha1hex("a", "c")
    assert sha1hex("ab") != sha1hex("a", "b")


def test_disk_cache_round_trip_and_persistence(tmp_path):
    c = DiskCache("demo", tmp_path)
    assert c.get("k") is None
    c.set("k", {"text": "hello", "ids": [1, 2]})
    assert c.get("k") == {"text": "hello", "ids": [1, 2]}
    c2 = DiskCache("demo", tmp_path)
    assert c2.get("k") == {"text": "hello", "ids": [1, 2]}
    assert (tmp_path / "demo.json").is_file()


def test_disk_cache_survives_corrupt_file(tmp_path):
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    c = DiskCache("bad", tmp_path)
    assert c.get("anything") is None
    c.set("k", 1)
    assert c.get("k") == 1


def test_embed_cache_serves_hits_without_api(monkeypatch, tmp_path):
    """Simulate the embedder's cache partitioning with a counting fetcher."""
    monkeypatch.setattr(cache_module, "DEFAULT_CACHE_DIR", tmp_path)
    reset_caches()
    try:
        from app.cache import get_cache
        from app.cache import sha1hex as h

        calls: list[list[str]] = []

        def fake_fetch(texts):
            calls.append(list(texts))
            return [[float(len(t))] * 4 for t in texts]

        def cached_embed_batch(model, texts):
            cache = get_cache("embeddings")
            out: list = [None] * len(texts)
            missing: list[int] = []
            for i, t in enumerate(texts):
                vec = cache.get("emb:" + model + ":" + h(t))
                if vec is not None:
                    out[i] = vec
                else:
                    missing.append(i)
            for i in missing:
                out[i] = fake_fetch([texts[i]])[0]
                cache.set("emb:" + model + ":" + h(texts[i]), out[i])
            return out

        first = cached_embed_batch("m", ["alpha", "beta"])
        assert len(calls) == 2
        second = cached_embed_batch("m", ["alpha", "beta", "gamma"])
        assert len(calls) == 3  # only gamma fetched
        assert second[0] == first[0] and second[1] == first[1]
        assert get_cache("embeddings").hits >= 2
    finally:
        reset_caches()


def test_cache_files_are_plain_json(tmp_path):
    c = DiskCache("plain", tmp_path)
    c.set("k", [1.5, 2.5])
    raw = json.loads((tmp_path / "plain.json").read_text(encoding="utf-8"))
    assert raw == {"k": [1.5, 2.5]}
