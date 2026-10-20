"""The retrieval store: Chroma persistent client, scope + strict-T enforced.

    app/retrieval/
        store.py    -- ChromaVectorStore: the two obligations live here
        service.py  -- RetrievalService: awaitable retrieve() for ChatService
        registry.py -- process-wide shared store + snapshot bookkeeping

Enforcement, and where it happens
--------------------------------
Both obligations are pushed into the Chroma ``where`` filter so they are part
of the query, not a post-filter someone can forget:

1. **Scope.** ``resource_id IN resource_scope``. A chunk from an ungranted
   resource is never a candidate, so it cannot appear in a citation.
2. **Strict-T.** ``end_ms IS NOT NULL AND end_ms <= T`` for time-gated source
   types; docs (pdf/pptx) are ungated because a page is not a moment in time.
   Never ``start_ms`` -- a gate relaxed to ``start_ms`` measured a 48% spoiler
   rate (ADR-002 §5.1).

``app/chat/service.py:apply_strict_timestamp`` re-checks strict-T after
retrieval as a second, independent gate. The redundancy is deliberate: each
layer has failed independently at some point in this project's history.

Chroma cannot express ``IS NOT NULL`` (metadata values are non-null by
construction, and a missing key simply fails a comparison), so unknown
``end_ms`` is stored as a sentinel and the filter requires the real integer.
Python-side re-filtering then happens as a *third*, cheap assertion layer --
not as the primary mechanism.

Two mechanisms address starvation, and only the second is sufficient on its own:

1. **Over-fetch** (recall). Request ``k * STARVATION_OVERFETCH`` neighbours so
   the eligible pool is large enough to contain video chunks at all. This is
   necessary: Chroma filters AFTER the ANN step, so with ``n_results=k`` the
   candidates can be entirely ineligible and the eligible set never surfaces.
2. **Per-family coverage** (the guarantee). Over-fetching alone is not enough
   -- the extra candidates are simply truncated away by top-k, and because
   slides restate the learner's question almost verbatim, docs outrank the
   transcript on pure distance. A learner at T=30s asking "what is on screen"
   would get slides back and an empty answer about the video.

   So the top-k is formed with a reserved slot per source family that is
   present *and eligible* in the candidate pool: distance order first, then a
   guaranteed slot for the best-scoring time-gated passage (and the best
   doc). Only families actually present in the pool get a slot, so this adds
   nothing when there is only one source type -- it is a recall guarantee
   across media kinds, not a re-ranking.

   It cannot weaken the gate: coverage is chosen only from passages that
   already passed ``where`` and :meth:`ChromaVectorStore._passes_strict_t`.

Low-T starvation (ADR-002 R1)
-----------------------------
Chroma/LangChain run approximate nearest-neighbour search and apply metadata
filters afterwards. At low ``T`` most in-scope chunks are docs (never gated) and
video chunks are a small minority, so a naive ``n_results=k`` query can be
entirely consumed by the *unfiltered-then-discarded* majority and return zero
video chunks even though plenty are eligible.

Mitigation: the query over-fetches (see :data:`STARVATION_OVERFETCH`) so the
filter has candidates to work with, and the Python re-filter guarantees
correctness of what survives. Over-fetching cannot leak -- the filter is still
applied inside Chroma -- it only makes the eligible set large enough to reach
``k``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from app.retrieval.recency import apply_recency_preference

log = logging.getLogger(__name__)

#: Source types subject to the strict-T gate. Must stay a superset of
#: ``app.chat.service.VIDEO_SOURCE_TYPES``; that layer independently re-checks
#: and would otherwise drop visual chunks before this layer ever sees them.
TIME_GATED_SOURCE_TYPES: frozenset[str] = frozenset(
    {"video", "video_transcript", "visual", "visual_frame"}
)

#: Sentinel stored for a time-gated chunk whose end is unknown. Any real
#: end_ms is >= 0, so ``end_ms == -1`` can never satisfy the ``<= T`` arm and
#: the chunk is excluded -- which is the required behaviour (NULL end_ms is
#: EXCLUDE, not UNKNOWN).
NO_END_MS = -1

#: How many neighbours to request per wanted passage. Chroma filters AFTER
#: ANN, so requesting only k can starve eligible video chunks behind ineligible
#: docs. 20x is generous for course-sized corpora and still cheap.
STARVATION_OVERFETCH = 20

#: Hard ceiling on the over-fetch, so a large k cannot ask for 10k vectors.
MAX_CANDIDATES = 2000

#: Over-fetch multiplier for present-tense questions. Higher than
#: :data:`STARVATION_OVERFETCH` because the query text ("what is on screen?")
#: is semantically distant from almost every chunk, so plain ANN ranking is
#: least likely to surface exactly the passages the recency re-rank needs.
RECENCY_OVERFETCH = 60


@dataclass
class RetrievedPassage:
    """A retrieved chunk, shaped as ``app.chat`` reads one.

    Field list is pinned by the readers: ``_passage_to_citation``,
    ``build_grounded_prompt`` and ``apply_strict_timestamp`` touch exactly
    these attributes.
    """

    chunk_id: str
    resource_id: str
    course_id: str
    source_type: str
    text: str
    #: How the evidence was produced: "transcript" (spoken audio) or
    #: "visual" (a sampled frame). Stored verbatim from chunk metadata, so
    #: "" means the producer did not record it -- never "transcript".
    #: Mirrors ``metadata["kind"]``; this is the typed handle the chat layer
    #: reads so it never has to reach into the raw dict.
    kind: str | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    video_id: str | None = None
    page_no: int | None = None
    slide_no: int | None = None
    #: 1-based position of an image within its resource. The image analogue
    #: of ``page_no``: an image has no timeline, so this is the only handle a
    #: citation has. ``None`` for every non-image source type.
    image_no: int | None = None
    score: float = 1.0
    metadata: dict = field(default_factory=dict)


def _image_no_of(chunk) -> int:
    """``chunk``'s image number, or the same ``-1`` sentinel the other
    positional fields use when the chunk is not an image.

    Read from metadata rather than a ``Chunk`` attribute: image ingest stamps
    it there so ``app.ingestion.chunking`` -- owned by another change -- is
    untouched. A non-image chunk therefore always yields ``-1`` and is
    indistinguishable in the filter from one that never had an image number.
    """
    if chunk.source_type != "image":
        return -1
    raw = (chunk.metadata or {}).get("image_no")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return -1


def chroma_metadata(chunk) -> dict:
    """Flatten a :class:`app.ingestion.chunking.Chunk` into Chroma metadata.

    ``end_ms`` becomes :data:`NO_END_MS` when unknown so the strict-T filter
    has a comparable integer to work with (Chroma has no null metadata).
    """
    gated = chunk.source_type in TIME_GATED_SOURCE_TYPES
    end_ms = chunk.end_ms if chunk.end_ms is not None else NO_END_MS
    meta = {
        "chunk_id": chunk.chunk_id,
        "resource_id": chunk.resource_id,
        "course_id": chunk.course_id,
        "source_type": chunk.source_type,
        "start_ms": -1 if chunk.start_ms is None else int(chunk.start_ms),
        "end_ms": int(end_ms),
        "page_no": -1 if chunk.page_no is None else int(chunk.page_no),
        "slide_no": -1 if chunk.slide_no is None else int(chunk.slide_no),
        # Same sentinel convention as page_no/slide_no. An image is ungated
        # (deliberately absent from TIME_GATED_SOURCE_TYPES), so this is a
        # citation handle, never a filter input.
        "image_no": _image_no_of(chunk),
        "video_id": chunk.video_id or "",
        "kind": str(chunk.metadata.get("kind", "")),
        # Original uploaded file name. Carried per chunk so a re-ingest
        # overwrites it; "" means the filename was genuinely unavailable and
        # the catalogue must show the resource id rather than invent one.
        "title": str(chunk.metadata.get("title", "") or ""),
    }
    # Processing-frontier markers, written only by the windowed video ingest.
    # Absent (not a sentinel) for every legacy chunk, so legacy resources stay
    # indistinguishable from fully processed ones.
    for key in ("processed_through_ms", "duration_ms", "visual_through_ms"):
        raw = (chunk.metadata or {}).get(key)
        if raw is not None:
            try:
                meta[key] = int(raw)
            except (TypeError, ValueError):
                pass
    log.debug("chroma metadata for %s gated=%s", chunk.chunk_id, gated)
    return meta


class _EmbedderFunction:
    """Adapter from our embedder to Chroma's ``EmbeddingFunction`` protocol.

    Supplied explicitly so Chroma never falls back to its default ONNX model,
    which downloads weights on first use and would break offline tests.
    """

    def __init__(self, embedder) -> None:
        self._embedder = embedder

    def __call__(self, input):
        return self._embedder.embed_documents(list(input))

    @staticmethod
    def name() -> str:
        return "ailearn"


def build_where(
    *,
    course_id: str,
    resource_scope: list[str],
    current_time_ms: int,
    focus_resource_id: str | None = None,
) -> dict:
    """The scope + strict-T predicate, as a Chroma ``where`` clause.

    Structure (ADR-002 §5.1 asks for the gate to be structural, not a missing
    ``AND``)::

        course_id == <course>
        AND resource_id IN <granted scope>
        AND ( NOT time_gated  OR  (end_ms != NO_END_MS AND end_ms <= T) )

    The third arm is the strict-T gate: it excludes unknown ends (sentinel)
    and everything after T, and lets docs through untouched.
    """
    granted = list(resource_scope or [])
    if focus_resource_id:
        # INTERSECTION, never replacement. Narrowing the query is what focus
        # means; widening it past the grant would be a scope bypass -- a
        # caller could focus an ungranted resource_id and read it.
        granted = [r for r in granted if r == focus_resource_id]
    # An empty grant means no resources are readable. Returning an impossible
    # filter is the fail-closed choice; letting an empty $in match everything
    # would hand back the whole corpus.
    if not granted:
        return {"resource_id": {"$in": ["__no_granted_resource__"]}}
    return {
        "$and": [
            {"course_id": {"$eq": course_id}},
            {"resource_id": {"$in": granted}},
            {
                "$or": [
                    {"source_type": {"$nin": list(TIME_GATED_SOURCE_TYPES)}},
                    {
                        "$and": [
                            {"end_ms": {"$ne": NO_END_MS}},
                            {"end_ms": {"$lte": int(current_time_ms)}},
                        ]
                    },
                ]
            },
        ]
    }


class ChromaVectorStore:
    """Chroma-backed chunk store with scope and strict-T enforced in-query."""

    def __init__(self, *, path: str | Path, embedder=None, collection: str = "chunks") -> None:
        import chromadb

        from app.ingestion.embedder import get_default_embedder

        self.path = str(path)
        self._embedder = embedder or get_default_embedder()
        self._client = chromadb.PersistentClient(path=self.path)
        self._collection = self._client.get_or_create_collection(
            name=collection,
            # Cosine distance: Chroma computes it. No hand-rolled similarity.
            metadata={"hnsw:space": "cosine"},
            embedding_function=_EmbedderFunction(self._embedder),
        )
        log.info("chroma store ready at %s", self.path)

    # -- writes ---------------------------------------------------------

    def add(self, chunks: list) -> int:
        """Upsert chunks. Re-ingesting a resource replaces its chunks."""
        if not chunks:
            return 0
        ids = [c.chunk_id for c in chunks]
        documents = [c.text for c in chunks]
        metadatas = [chroma_metadata(c) for c in chunks]
        embeddings = self._embedder.embed_documents(documents)
        self._collection.upsert(
            ids=ids, documents=documents, metadatas=metadatas, embeddings=embeddings
        )
        return len(chunks)

    def mark_progress(
        self, *, resource_id: str, course_id: str, kind: str, start_ms: int, frontier: dict
    ) -> None:
        """Record a window that produced no chunks, so the frontier still advances.

        Without this an empty window (silence, a static slide) leaves the
        frontier behind it and ``/position`` re-queues it forever. The marker
        can never be retrieved, twice over: ``end_ms`` is the NO_END_MS
        sentinel on a time-gated source type (the strict-T arm excludes it),
        and it has no ``chunk_id`` (``_to_passage`` drops it). Its fixed id
        per (resource, kind, window) makes a re-run overwrite it, and
        ``delete_resource`` removes it by the same exact-id filter as chunks.
        """
        meta = {
            "chunk_id": "",
            "resource_id": resource_id,
            "course_id": course_id,
            "source_type": "video",
            "start_ms": int(start_ms),
            "end_ms": NO_END_MS,
            "kind": "progress_marker",
            "title": "",
        }
        meta.update({k: int(v) for k, v in frontier.items() if v is not None})
        self._collection.upsert(
            ids=[f"{resource_id}__progress_{kind}_{int(start_ms)}"],
            documents=[""],
            metadatas=[meta],
            embeddings=[self._marker_vector()],
        )

    def _marker_vector(self) -> list[float]:
        # Any unit vector of the collection's width; borrowed from a stored
        # record so no embedding request is spent on a marker.
        got = self._collection.get(limit=1, include=["embeddings"])
        embs = got.get("embeddings")
        width = len(embs[0]) if embs is not None and len(embs) else len(
            self._embedder.embed_query("progress marker")
        )
        return [1.0] + [0.0] * (width - 1)

    def delete_resource(self, resource_id: str) -> None:
        self._collection.delete(where={"resource_id": {"$eq": resource_id}})

    def delete_resource_range(
        self, resource_id: str, start_ms: int, end_ms: int, kind: str | None = None
    ) -> int:
        """Delete chunks of EXACTLY ``resource_id`` lying within [start_ms, end_ms].

        A chunk is in range when ``start_ms <= chunk.start_ms`` and
        ``chunk.end_ms <= end_ms``; chunks with unknown (sentinel) bounds are
        never matched. Exact ``resource_id`` equality only -- no course or
        prefix sweeps. Lets a re-run window replace its chunks without leaving
        stale ones. ``kind`` ("transcript" | "visual"), when given, restricts
        the delete to that stage's chunks; ``None`` deletes every kind.
        Returns the number deleted.
        """
        if not resource_id:
            return 0
        clauses = [
            {"resource_id": {"$eq": resource_id}},
            {"start_ms": {"$gte": int(start_ms)}},
            {"end_ms": {"$ne": NO_END_MS}},
            {"end_ms": {"$lte": int(end_ms)}},
        ]
        if kind is not None:
            clauses.append({"kind": {"$eq": str(kind)}})
        where = {"$and": clauses}
        got = self._collection.get(where=where, include=[])
        ids = list(got.get("ids") or [])
        if ids:
            self._collection.delete(ids=ids)
        return len(ids)

    def processing_frontier(self, resource_id: str) -> dict | None:
        """Frontier facts for one exact resource, or ``None`` if unknown.

        Returns ``{"source_type", "processed_through_ms", "duration_ms"}``;
        the two ms values are the MAX over the resource's chunks, ``None``
        when no chunk carries them. Catalog lookup only: no gate involved.
        """
        if not resource_id:
            return None
        try:
            got = self._collection.get(
                where={"resource_id": {"$eq": resource_id}}, include=["metadatas"]
            )
        except Exception as exc:  # noqa: BLE001 - must not 500 chat
            log.warning("processing_frontier failed for %s: %s", resource_id, exc)
            return None
        out: dict | None = None
        for meta in got.get("metadatas") or []:
            if not meta or meta.get("resource_id") != resource_id:
                continue
            if out is None:
                out = {
                    "source_type": meta.get("source_type") or None,
                    "processed_through_ms": None,
                    "duration_ms": None,
                    "visual_through_ms": None,
                }
            for key in ("processed_through_ms", "duration_ms", "visual_through_ms"):
                v = meta.get(key)
                if isinstance(v, int) and (out[key] is None or v > out[key]):
                    out[key] = v
        return out

    def count(self) -> int:
        return self._collection.count()

    def resource_source_type(self, resource_id: str) -> str | None:
        """The ``source_type`` this resource's chunks were indexed under.

        Additive pass-through used by the live-frame path to decide whether a
        resource has a timeline at all: ``video`` does, ``pdf``/``pptx``/``image``
        do not, and an unrecognised resource must not be guessed into one.
        Reads one chunk's metadata by an exact ``resource_id`` equality filter
        (never by course or prefix -- see the deletion rule above) and returns
        ``None`` when the resource is unknown or carries no metadata.

        Touches nothing in the strict-T gate: this is a catalog lookup, not a
        query, and no timestamp is compared.
        """
        if not resource_id:
            return None
        try:
            got = self._collection.get(
                where={"resource_id": {"$eq": resource_id}},
                limit=1,
                include=["metadatas"],
            )
        except Exception as exc:  # noqa: BLE001 - a catalog miss must not 500
            log.warning("resource_source_type failed for %s: %s", resource_id, exc)
            return None
        for meta in got.get("metadatas") or []:
            if meta and meta.get("resource_id") == resource_id:
                return meta.get("source_type") or None
        return None

    def resources(self, course_id: str) -> list[dict]:
        """Distinct resources in a course, for ``GET /courses/{id}/resources``.

        ``title`` is the original uploaded file name carried on the chunks.
        ``kind`` (an internal extractor label such as "transcript") is NOT a
        usable title -- it was previously surfaced verbatim and every learner
        saw the word "transcript" instead of their own file name. When no
        chunk carries a filename, ``title`` is ``None`` so the UI can fall back
        to the resource id; a title is never invented from the source type.
        """
        seen: dict[str, dict] = {}
        try:
            got = self._collection.get(include=["metadatas"])
        except Exception as exc:  # noqa: BLE001 - listing must not 500
            log.warning("chroma resources listing failed: %s", exc)
            return []
        for meta in got.get("metadatas") or []:
            if not meta or meta.get("course_id") != course_id:
                continue
            rid = meta.get("resource_id")
            if not rid:
                continue
            # `youtube_id` is the exact field name apps/web reads; the
            # store's internal key is `video_id`. Renaming here rather
            # than in the router keeps the shape next to its source.
            provider_video_id = meta.get("video_id") or None
            entry = seen.get(rid)
            if entry is None:
                seen[rid] = {
                    "id": rid,
                    "type": meta.get("source_type", ""),
                    "provider": "youtube" if provider_video_id else "file",
                    "youtube_id": provider_video_id,
                    "title": (meta.get("title") or None),
                    "status": "ready",
                    "asset_name": None,
                    "_frontier_ms": None,
                    "_duration_ms": None,
                    "_visual_ms": None,
                }
                self._fold_frontier(seen[rid], meta)
                continue
            self._fold_frontier(entry, meta)
            # Chunks of one resource all carry the same filename, but a partial
            # re-ingest can leave some chunks without one. Keep the first real
            # title found rather than letting an empty one overwrite it.
            if not entry["title"] and meta.get("title"):
                entry["title"] = str(meta["title"])
        out = list(seen.values())
        for entry in out:
            # DURATION SOURCE: the `duration_ms` the windowed ingest stamps on
            # chunk metadata (probed from the container). The max video
            # end_ms is NOT used: it equals the processed frontier, so it could
            # never read as partial. Unknown duration => stays "ready".
            duration = entry.pop("_duration_ms")
            frontier = entry.pop("_frontier_ms")
            visual_through = entry.pop("_visual_ms")
            if visual_through is not None:
                entry["visual_through_ms"] = visual_through
            if frontier is not None:
                # Key omitted for legacy resources so their shape is unchanged
                # (the subjects view mirrors this shape exactly).
                entry["processed_through_ms"] = frontier
            if frontier is not None and duration is not None and frontier < duration:
                entry["status"] = "partial"
        return out

    @staticmethod
    def _fold_frontier(entry: dict, meta: dict) -> None:
        for key, slot in (
            ("processed_through_ms", "_frontier_ms"),
            ("duration_ms", "_duration_ms"),
            ("visual_through_ms", "_visual_ms"),
        ):
            v = meta.get(key)
            if isinstance(v, int) and (entry[slot] is None or v > entry[slot]):
                entry[slot] = v

    # -- reads ----------------------------------------------------------

    def query(
        self,
        query_text: str,
        *,
        course_id: str,
        resource_scope: list[str],
        current_time_ms: int,
        k: int = 8,
        focus_resource_id: str | None = None,
        prefer_recent: bool = False,
    ) -> list[RetrievedPassage]:
        """Vector search under the scope + strict-T predicate.

        Over-fetches to survive Chroma's post-ANN metadata filtering, then
        applies the per-family coverage guarantee and re-asserts strict-T in
        Python. See the module docstring on low-T starvation for why both
        steps are needed.

        ``prefer_recent`` re-ranks the ALREADY-GATED eligible pool toward the
        playhead for present-tense questions ("what is on screen right now?").
        It runs after :meth:`_passes_strict_t` and can only narrow or reorder
        that pool -- it cannot admit a passage the gate excluded. See
        ``app/retrieval/recency.py``.
        """
        where = build_where(
            course_id=course_id,
            resource_scope=resource_scope,
            current_time_ms=current_time_ms,
            focus_resource_id=focus_resource_id,
        )
        candidates = min(max(k * STARVATION_OVERFETCH, k), MAX_CANDIDATES)
        if prefer_recent:
            # A present-tense query ("what is on screen?") is lexically close to
            # almost nothing, so ANN alone may not surface the chunks at the
            # playhead at all -- a preference cannot rescue a chunk that was
            # never returned. Over-fetching more for these queries keeps the
            # recent passages inside the pool that the re-rank can choose from.
            # This is recall only: the same ``where`` clause, and therefore the
            # same strict-T gate, applies unchanged.
            candidates = min(max(k * RECENCY_OVERFETCH, k), MAX_CANDIDATES)
        embedding = self._embedder.embed_query(query_text)
        got = self._collection.query(
            query_embeddings=[embedding],
            n_results=candidates,
            where=where,
            include=["documents", "metadatas", "distances"],
        )

        docs = (got.get("documents") or [[]])[0]
        metas = (got.get("metadatas") or [[]])[0]
        dists = (got.get("distances") or [[]])[0]

        # Every eligible passage in distance order. The gate is applied here,
        # before any selection, so coverage can only ever pick from passages
        # that were already proven eligible.
        eligible: list[RetrievedPassage] = []
        for doc, meta, dist in zip(docs, metas, dists):
            passage = self._to_passage(doc, meta or {}, float(dist))
            if passage is None:
                continue
            # Third gate layer: cheap, and it makes a filter regression fail
            # loudly here rather than silently serving a spoiler.
            if not self._passes_strict_t(passage, current_time_ms):
                log.warning("post-filter dropped %s (strict-T)", passage.chunk_id)
                continue
            eligible.append(passage)

        if prefer_recent:
            # STRICTLY DOWNSTREAM of the gate. `eligible` contains only
            # passages that already passed `where` + `_passes_strict_t`, so
            # this can only drop stale ones or reorder -- never admit a future
            # one. An empty result here is the honest "nothing is happening at
            # the playhead" case and surfaces as the ordinary refusal.
            eligible = apply_recency_preference(eligible, current_time_ms)
            if not eligible:
                log.info(
                    "recency: no passage within the recency floor of T=%s; "
                    "refusing rather than answering from stale material",
                    current_time_ms,
                )

        return self._select_with_coverage(eligible, k)

    @staticmethod
    def _select_with_coverage(
        eligible: list[RetrievedPassage], k: int
    ) -> list[RetrievedPassage]:
        """Top-k by distance, with a reserved slot per present source family.

        Distance order is preserved for the slots that are not reserved, so
        this is a recall guarantee across media kinds rather than a re-ranking:
        the strongest passage of a missing family is appended at the end, and
        only for families that are actually present in the eligible pool.

        With a single source type this is exactly plain top-k.
        """
        if not eligible or k <= 0:
            return []
        if len(eligible) <= k:
            return list(eligible)  # everything fits; no coverage needed

        def family(p: RetrievedPassage) -> str:
            return "gated" if p.source_type in TIME_GATED_SOURCE_TYPES else "doc"

        present = {family(p) for p in eligible}
        # Best passage per family, in distance order, for families present in
        # the eligible pool but missing from a plain top-k.
        head_ids = {p.chunk_id for p in eligible[:k]}
        reserved: list[RetrievedPassage] = []
        for name in ("gated", "doc"):
            if name not in present:
                continue
            best = next(
                (p for p in eligible if family(p) == name and p.chunk_id not in head_ids),
                None,
            )
            if best is None:
                continue
            log.info(
                "coverage: reserving a slot for a %s source (%s) -- a plain "
                "top-k would answer from one medium only",
                name, best.chunk_id,
            )
            reserved.append(best)

        # Give up the least relevant slots for the reserved ones. `reserved`
        # only ever holds passages that already passed the gate.
        room = max(k - len(reserved), 1)
        chosen = eligible[:room]
        for passage in reserved:
            if len(chosen) < k:
                chosen.append(passage)
            else:
                chosen[-1] = passage
        return chosen[:k]

    @staticmethod
    def _to_passage(doc: str, meta: dict, distance: float) -> RetrievedPassage | None:
        chunk_id = meta.get("chunk_id")
        if not chunk_id:
            return None
        gated = meta.get("source_type") in TIME_GATED_SOURCE_TYPES
        raw_end = meta.get("end_ms")
        end_ms = None
        if gated:
            end_ms = None if raw_end in (None, NO_END_MS) else int(raw_end)
        else:
            end_ms = None
        raw_start = meta.get("start_ms")
        start_ms = None if raw_start in (None, -1) else int(raw_start)
        raw_page = meta.get("page_no")
        raw_slide = meta.get("slide_no")
        raw_image = meta.get("image_no")
        return RetrievedPassage(
            chunk_id=chunk_id,
            resource_id=meta.get("resource_id", ""),
            course_id=meta.get("course_id", ""),
            source_type=meta.get("source_type", ""),
            text=doc or "",
            # "" in Chroma means "not recorded"; normalise to None so the
            # chat layer can tell "unknown" apart from an empty string.
            kind=meta.get("kind") or None,
            start_ms=start_ms,
            end_ms=end_ms,
            video_id=meta.get("video_id") or None,
            page_no=None if raw_page in (None, -1) else int(raw_page),
            slide_no=None if raw_slide in (None, -1) else int(raw_slide),
            image_no=None if raw_image in (None, -1) else int(raw_image),
            # Cosine distance -> similarity, for callers that rank by score.
            score=1.0 - distance,
            metadata={"kind": meta.get("kind", "")},
        )

    @staticmethod
    def _passes_strict_t(passage: RetrievedPassage, current_time_ms: int) -> bool:
        """Independent re-check of the gate. Same semantics as the store query."""
        if passage.source_type not in TIME_GATED_SOURCE_TYPES:
            return True
        return (
            passage.end_ms is not None
            and int(passage.end_ms) <= int(current_time_ms)
        )


def assert_no_leakage(
    passages: list[RetrievedPassage],
    *,
    resource_scope: list[str],
    current_time_ms: int,
) -> None:
    """Post-retrieval assertion. Raises on any leak -- never repairs silently.

    A leak here is a spoiler leak, so the failure mode must be loud
    (``AssertionError`` into the request handler / log) rather than a filter
    that quietly drops the offending passage and hides a regression.
    """
    granted = set(resource_scope or [])
    for p in passages:
        assert p.resource_id in granted, f"scope leak: {p.resource_id} not granted"
        if p.source_type in TIME_GATED_SOURCE_TYPES:
            assert p.end_ms is not None, f"strict-T leak: {p.chunk_id} has NULL end_ms"
            assert int(p.end_ms) <= int(current_time_ms), (
                f"strict-T leak: {p.chunk_id} ends at {p.end_ms} > {current_time_ms}"
            )