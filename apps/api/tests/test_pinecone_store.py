"""Pinecone adapter: same answers as Chroma, gate re-checked. Fake index only."""

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from app.retrieval.pinecone_store import PineconeVectorStore, matches
from app.retrieval.store import ChromaVectorStore, build_where
from tests.store_helpers import make_chunk, make_embedder


class FakeIndex:
    """In-memory stand-in honouring the Pinecone calls the adapter makes.

    ``filter`` is evaluated with the same operator semantics Pinecone
    documents. ``leak`` makes it ignore the filter, to prove the Python
    re-check still holds the gate if the server-side filter ever fails.
    """

    def __init__(self, dimension: int) -> None:
        self.dimension = dimension
        self.rows: dict[str, tuple[list[float], dict]] = {}
        self.filters: list[dict | None] = []
        self.leak = False

    def list(self):
        ids = list(self.rows)
        for i in range(0, len(ids), 3):  # small pages exercise pagination
            yield ids[i : i + 3]

    def fetch(self, ids):
        return SimpleNamespace(vectors={
            i: SimpleNamespace(metadata=dict(self.rows[i][1])) for i in ids if i in self.rows
        })

    def upsert(self, vectors):
        for v in vectors:
            # Pinecone returns numbers as floats.
            meta = {k: float(x) if isinstance(x, int) else x for k, x in v["metadata"].items()}
            self.rows[v["id"]] = (v["values"], meta)

    def delete(self, ids):
        for i in ids:
            self.rows.pop(i, None)

    def query(self, *, vector, top_k, filter, include_metadata, include_values):
        self.filters.append(filter)

        def cos(a, b):
            na = math.sqrt(sum(x * x for x in a)) or 1.0
            nb = math.sqrt(sum(x * x for x in b)) or 1.0
            return sum(x * y for x, y in zip(a, b)) / (na * nb)

        hits = [
            SimpleNamespace(id=i, score=cos(vector, vals), metadata=dict(meta))
            for i, (vals, meta) in self.rows.items()
            if self.leak or matches(meta, filter)
        ]
        hits.sort(key=lambda h: -h.score)
        return SimpleNamespace(matches=hits[:top_k])


def _chunks():
    return [
        make_chunk("c_early", start_ms=0, end_ms=60_000, text="gradient of a surface"),
        make_chunk("c_mid", start_ms=60_000, end_ms=120_000, text="gradient chain rule"),
        make_chunk("c_late", start_ms=120_000, end_ms=180_000, text="gradient spoiler later"),
        make_chunk("c_noend", start_ms=0, end_ms=None, text="gradient unknown end"),
        make_chunk("c_pdf", source_type="pdf", start_ms=None, end_ms=None, page_no=2,
                   resource_id="res_pdf", text="gradient notes page"),
        make_chunk("c_other", resource_id="res_other", text="gradient other resource"),
    ]


@pytest.fixture
def stores(tmp_path):
    emb = make_embedder()
    chroma = ChromaVectorStore(path=tmp_path / "c", embedder=emb, collection="parity")
    index = FakeIndex(dimension=256)
    pine = PineconeVectorStore(index=index, embedder=emb)
    chroma.add(_chunks())
    pine.add(_chunks())
    return chroma, pine, index


def _ids(store, t, scope=("res_video", "res_pdf")):
    hits = store.query("gradient", course_id="course_1", resource_scope=list(scope),
                       current_time_ms=t, k=10)
    return sorted(h.chunk_id for h in hits)


@pytest.mark.parametrize("t", [0, 60_000, 119_999, 120_000, 10**7])
def test_same_gated_set_as_chroma(stores, t):
    chroma, pine, _ = stores
    assert _ids(pine, t) == _ids(chroma, t)
    assert "c_noend" not in _ids(pine, t) and "c_other" not in _ids(pine, t)


def test_strict_t_filter_is_sent_to_pinecone(stores):
    _, pine, index = stores
    pine.query("gradient", course_id="course_1", resource_scope=["res_video"],
               current_time_ms=90_000, k=5)
    assert index.filters[-1] == build_where(
        course_id="course_1", resource_scope=["res_video"], current_time_ms=90_000
    )


def test_python_recheck_holds_if_server_filter_leaks(stores):
    _, pine, index = stores
    index.leak = True
    got = _ids(pine, 60_000)
    assert got == ["c_early", "c_pdf"]  # no late, no unknown end, no other scope


def test_mirror_survives_restart_and_catalogue_matches(stores):
    chroma, _pine, index = stores
    reopened = PineconeVectorStore(index=index, embedder=make_embedder())
    assert reopened.count() == chroma.count() == 6
    assert reopened.resource_source_type("res_pdf") == "pdf"

    def by_id(rows):
        return sorted(r["id"] for r in rows)

    assert by_id(reopened.resources("course_1")) == by_id(chroma.resources("course_1"))


def test_deletes_are_exact_resource_equality(stores):
    _, pine, index = stores
    pine.add([make_chunk("c_prefix", resource_id="res_video_2")])
    pine.delete_resource_range("res_video", 0, 120_000)
    assert {"c_early", "c_mid"}.isdisjoint(index.rows)
    assert {"c_late", "c_noend", "c_prefix"} <= set(index.rows)
    pine.delete_resource("res_video")
    assert "c_prefix" in index.rows and "c_pdf" in index.rows
    assert not any(m.get("resource_id") == "res_video" for _, m in index.rows.values())
