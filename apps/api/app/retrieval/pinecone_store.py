"""Pinecone-backed chunk store (``VECTOR_STORE=pinecone``).

The hosted deployment has no persistent disk, so the index lives in Pinecone.
Rather than re-implementing retrieval, this module adapts a Pinecone index to
the small slice of Chroma's collection API that :class:`ChromaVectorStore`
uses (``upsert``/``get``/``query``/``delete``/``count``). Every rule above the
collection -- the scope + strict-T ``where`` clause from :func:`build_where`,
the Python re-check, coverage selection, recency, frontier and catalogue
logic -- is therefore the exact same code path the 400+ tests exercise.

* ``query`` sends :func:`build_where` to Pinecone as the metadata filter.
  Pinecone's filter language uses the same operators (``$eq $ne $in $nin $lte
  $gte $and $or``), so the strict-T gate runs server-side unchanged, and
  ``ChromaVectorStore._passes_strict_t`` still re-checks every passage.
* Catalogue reads (``get``) and range deletes need exact metadata scans, which
  Pinecone does not offer. This process is the index's only writer, so it keeps
  an in-memory mirror of every record's metadata, loaded once at start-up
  (list + fetch) and updated on every write. Deletes resolve ids from the
  mirror with exact equality, then delete by id.
"""

from __future__ import annotations

import logging
import threading

from app.retrieval.store import ChromaVectorStore

log = logging.getLogger(__name__)

#: Document text rides in metadata (Pinecone stores no separate document).
TEXT_KEY = "_text"
#: Vectors per upsert request. A 3072-d vector is ~30 KB of JSON and a request
#: is capped at 2 MB.
UPSERT_BATCH = 40
FETCH_BATCH = 100
DELETE_BATCH = 1000
#: Pinecone's top_k ceiling when metadata is included.
MAX_TOP_K = 1000


def matches(meta: dict, where: dict | None) -> bool:
    """Evaluate a Chroma/Pinecone ``where`` clause against one metadata dict."""
    if not where:
        return True
    for key, cond in where.items():
        if key == "$and":
            if not all(matches(meta, c) for c in cond):
                return False
            continue
        if key == "$or":
            if not any(matches(meta, c) for c in cond):
                return False
            continue
        value = meta.get(key)
        if not isinstance(cond, dict):
            cond = {"$eq": cond}
        for op, arg in cond.items():
            if op == "$eq":
                ok = value == arg
            elif op == "$ne":
                ok = value != arg
            elif op == "$in":
                ok = value in arg
            elif op == "$nin":
                ok = value not in arg
            elif op in ("$gt", "$gte", "$lt", "$lte"):
                if not isinstance(value, (int, float)):
                    return False
                ok = {
                    "$gt": value > arg, "$gte": value >= arg,
                    "$lt": value < arg, "$lte": value <= arg,
                }[op]
            else:
                raise ValueError(f"unsupported filter operator {op}")
            if not ok:
                return False
    return True


def _as_int(meta: dict) -> dict:
    # Pinecone returns every number as a float; the store compares ints.
    return {
        k: int(v) if isinstance(v, float) and v.is_integer() else v
        for k, v in meta.items()
    }


class PineconeCollection:
    """The Chroma collection surface ``ChromaVectorStore`` calls, over Pinecone."""

    def __init__(self, index, *, dimension: int) -> None:
        self._index = index
        self.dimension = dimension
        self._lock = threading.Lock()
        self._mirror: dict[str, dict] = {}
        self._load_mirror()

    def _load_mirror(self) -> None:
        ids: list[str] = []
        for page in self._index.list():
            # Older clients yield id strings; newer ones yield items with .id.
            ids.extend(getattr(item, "id", item) for item in page)
        for i in range(0, len(ids), FETCH_BATCH):
            got = self._index.fetch(ids=ids[i : i + FETCH_BATCH])
            for vid, vec in (got.vectors or {}).items():
                self._mirror[vid] = _as_int(dict(vec.metadata or {}))
        log.info("pinecone mirror loaded: %d record(s)", len(self._mirror))

    # -- writes ---------------------------------------------------------

    def upsert(self, *, ids, documents, metadatas, embeddings) -> None:
        rows = []
        for vid, doc, meta, emb in zip(ids, documents, metadatas, embeddings):
            full = {**meta, TEXT_KEY: doc or ""}
            rows.append({"id": vid, "values": [float(x) for x in emb], "metadata": full})
        for i in range(0, len(rows), UPSERT_BATCH):
            self._index.upsert(vectors=rows[i : i + UPSERT_BATCH])
        with self._lock:
            for row in rows:
                self._mirror[row["id"]] = dict(row["metadata"])

    def delete(self, *, ids=None, where=None) -> None:
        if ids is None:
            with self._lock:
                ids = [vid for vid, m in self._mirror.items() if matches(m, where)]
        ids = list(ids)
        for i in range(0, len(ids), DELETE_BATCH):
            self._index.delete(ids=ids[i : i + DELETE_BATCH])
        with self._lock:
            for vid in ids:
                self._mirror.pop(vid, None)

    # -- reads ----------------------------------------------------------

    def get(self, *, where=None, include=None, limit=None, ids=None) -> dict:
        include = include or []
        with self._lock:
            rows = [
                (vid, m) for vid, m in self._mirror.items()
                if (ids is None or vid in ids) and matches(m, where)
            ]
        if limit is not None:
            rows = rows[:limit]
        out: dict = {"ids": [vid for vid, _ in rows]}
        if "metadatas" in include:
            out["metadatas"] = [
                {k: v for k, v in m.items() if k != TEXT_KEY} for _, m in rows
            ]
        if "documents" in include:
            out["documents"] = [m.get(TEXT_KEY, "") for _, m in rows]
        if "embeddings" in include:
            # Values are not mirrored; callers only need the width.
            out["embeddings"] = [[0.0] * self.dimension for _ in rows]
        return out

    def query(self, *, query_embeddings, n_results, where=None, include=None) -> dict:
        res = self._index.query(
            vector=[float(x) for x in query_embeddings[0]],
            top_k=min(int(n_results), MAX_TOP_K),
            filter=where or None,
            include_metadata=True,
            include_values=False,
        )
        docs, metas, dists = [], [], []
        for m in res.matches or []:
            meta = _as_int(dict(m.metadata or {}))
            # The filter ran on a remote service; re-evaluate all of it (scope
            # as well as strict-T) here before anything reaches the store.
            if not matches(meta, where):
                log.warning("pinecone returned %s outside the filter; dropped", m.id)
                continue
            docs.append(meta.pop(TEXT_KEY, ""))
            metas.append(meta)
            # Cosine similarity -> Chroma-style cosine distance.
            dists.append(1.0 - float(m.score))
        return {"documents": [docs], "metadatas": [metas], "distances": [dists]}

    def count(self) -> int:
        with self._lock:
            return len(self._mirror)


class PineconeVectorStore(ChromaVectorStore):
    """``ChromaVectorStore`` with its collection backed by a Pinecone index."""

    def __init__(self, *, index=None, api_key: str = "", index_name: str = "", embedder=None) -> None:
        from app.ingestion.embedder import get_default_embedder

        self._embedder = embedder or get_default_embedder()
        if index is None:
            from pinecone import Pinecone

            pc = Pinecone(api_key=api_key)
            dimension = int(pc.describe_index(index_name).dimension)
            index = pc.Index(index_name)
        else:
            dimension = int(getattr(index, "dimension", 0) or 0)
        width = getattr(self._embedder, "dim", None)
        if width and dimension and int(width) != dimension:
            # Vectors of one width compared against an index of another are
            # meaningless; refuse to start rather than index garbage.
            raise RuntimeError(
                f"pinecone index {index_name!r} is {dimension}-d but the "
                f"embedder produces {width}-d vectors; point PINECONE_INDEX at "
                f"an index of the embedder's width"
            )
        self.path = f"pinecone:{index_name or 'injected'}"
        self._collection = PineconeCollection(index, dimension=dimension)
        log.info("pinecone store ready (%s, dim=%d)", self.path, dimension)

    def _marker_vector(self) -> list[float]:
        return [1.0] + [0.0] * (self._collection.dimension - 1)
