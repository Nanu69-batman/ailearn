"""Offline evaluation harness for the golden QA set (Phase 6 eval assets).

Runs fully offline: fixture chunks are built programmatically, retrieval uses
a temporary ``ChromaVectorStore`` + ``HashingEmbedder`` (no network), and every
retrieved set is checked by the eval-strict leakage detector below.

Eval-strict leakage rule (identical to the production gate since the
end_ms-tightening): a retrieved ``video``/``video_transcript`` chunk must
satisfy ``end_ms <= pause_at_ms`` and belong to the learner's allowed
resources. The harness enforces strict visibility scoping *before* ranking
(``apply_strict_cutoff``) and re-asserts on the final set. The diagnostic
``production_gap_rate`` re-runs raw production retrieval over the full pool
under the same strict rule — it must be 0 and any nonzero value is a
production/harness parity regression, not a scoring input.

Proxy-gap note (review M6): ``DEFAULT_THRESHOLD`` (score cut for the offline
refusal proxy) and the ``citation_supported`` token-overlap minimum
(``min_overlap=2``) are unvalidated heuristic constants — they have not been
tuned against human relevance judgments. Likewise ``should_refuse`` here is a
retrieval-score proxy, NOT the production LLM refusal path (prompt +
allowlist + uncited-answer handling in ``app.chat.service``); treat
``refusal_correctness`` as a retrieval-gating signal only. See
``submission/eval/threshold-note.md`` for a small threshold-sensitivity sweep.

Usage (from ``apps/api``)::

    python -m app.eval.harness
    python -m app.eval.harness --golden ../../submission/eval/golden.jsonl --k 5

    # The real demo course (the lecture video + UNIT-1 PDF, exported chunks),
    # with the production embedder (needs PINECONE_API_KEY):
    python -m app.eval.harness \\
        --golden ../../submission/eval/golden-partial-derivatives.jsonl \\
        --chunks ../../submission/eval/partial-derivatives-chunks.jsonl \\
        --embedder pinecone
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.eval.fixture import (
    ALLOWED_RESOURCES,
    FIXTURE_COURSE_ID,
    build_fixture_chunks,
)
from app.ingestion.chunking import Chunk
from app.ingestion.embedder import HashingEmbedder
from app.retrieval.service import RetrievalService, RetrievedPassage
from app.retrieval.store import ChromaVectorStore

# Policy data: the harness tests `passage.source_type in VIDEO_SOURCE_TYPES`.
VIDEO_SOURCE_TYPES: frozenset[str] = frozenset({"video", "video_transcript"})

#: Reported in the scoreboard so a fixture-backed zero can never be mistaken for
#: a production-backed one. The parity pass runs against the real Chroma store.
GAP_MODE = "chroma"

DEFAULT_K = 5
DEFAULT_THRESHOLD = 0.08
GOLDEN_DEFAULT = (
    Path(__file__).resolve().parents[4] / "submission" / "eval" / "golden.jsonl"
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    ["the", "a", "an", "and", "or", "of", "to", "in", "on", "at", "for", "with", "what", "which", "who", "whom", "whose", "when", "where", "how", "why", "does", "do", "did", "is", "are", "was", "were", "be", "been", "being", "it", "its", "this", "that", "those", "these", "as", "by", "from", "into", "during", "following", "up", "out", "about", "above", "after", "before", "there", "their", "they", "them", "then", "than", "so", "such", "no", "nor", "not", "only", "own", "same", "too", "very", "can", "will", "just", "should", "now", "following"]
)


def content_tokens(text: str) -> set[str]:
    """Lowercase alphanumeric tokens minus stopwords and short tokens."""
    return {
        tok for tok in _TOKEN_RE.findall(text.lower()) if len(tok) > 3 and tok not in _STOPWORDS
    }


@dataclass
class LeakageFinding:
    chunk_id: str
    reason: str  # "timestamp" | "scope"
    detail: str = ""


def detect_leakage(
    retrieved: list[RetrievedPassage],
    pause_at_ms: int,
    allowed_resource_ids: list[str],
    resource_of: dict[str, str] | None = None,
) -> list[LeakageFinding]:
    """Eval-strict leakage check on an already-gated retrieved set.

    Flags any video chunk with ``end_ms > pause_at_ms`` (straddles or follows
    the pause), any ``video``/``video_transcript`` chunk with ``end_ms is None``
    (NULL end cannot prove ``end_ms <= T``, so production drops it — seeing one
    here is leakage), and any chunk outside the learner's resource scope.
    ``resource_of`` optionally maps chunk_id -> resource_id (used when passage
    objects do not carry it); ``RetrievedPassage`` has no resource field, so
    callers should pass the harness chunk lookup.
    """
    allowed = set(allowed_resource_ids or [])
    findings: list[LeakageFinding] = []
    for passage in retrieved:
        resource_id = (resource_of or {}).get(passage.chunk_id, "")
        if resource_id and resource_id not in allowed:
            findings.append(
                LeakageFinding(
                    chunk_id=passage.chunk_id,
                    reason="scope",
                    detail=f"resource {resource_id!r} outside learner scope",
                )
            )
            continue
        if passage.source_type in VIDEO_SOURCE_TYPES:
            if passage.end_ms is None:
                findings.append(
                    LeakageFinding(
                        chunk_id=passage.chunk_id,
                        reason="timestamp",
                        detail="end_ms=NULL",
                    )
                )
            elif int(passage.end_ms) > int(pause_at_ms):
                findings.append(
                    LeakageFinding(
                        chunk_id=passage.chunk_id,
                        reason="timestamp",
                        detail=f"end_ms={passage.end_ms} > T={pause_at_ms}",
                    )
                )
    return findings


def apply_strict_cutoff(
    chunks,
    pause_at_ms: int,
    allowed_resource_ids: list[str],
):
    """Eval-strict visibility scoping (filter-before-rank).

    Keeps a chunk only when it is inside the learner's resource scope and, for
    ``video``/``video_transcript`` chunks, fully watched (``end_ms <= T`` with
    ``end_ms`` non-NULL). A NULL-``end_ms`` video chunk cannot prove it ends
    before ``T`` so it is dropped (matching the production store gate, which
    excludes NULL ``end_ms``); non-transcript sources are unaffected by ``T``.
    This matches the production store gate (``docs/research-evaluation.md``
    strict rule); both enforce ``end_ms <= T`` with NULL excluded, so the
    pre-rank pool and production retrieval agree by construction.
    """
    allowed = set(allowed_resource_ids or [])
    t_ms = int(pause_at_ms)
    visible = []
    for chunk in chunks:
        if chunk.resource_id not in allowed:
            continue
        if chunk.source_type in VIDEO_SOURCE_TYPES:
            if chunk.end_ms is None:
                continue
            if int(chunk.end_ms) > t_ms:
                continue
        visible.append(chunk)
    return visible


def resolve_gold_chunk_ids(item: dict, chunks) -> list[str]:
    """Map an item's ``gold_sources`` entries onto fixture chunk ids."""
    wanted: list[str] = []
    for src in item.get("gold_sources", []):
        kind = src.get("kind")
        for chunk in chunks:
            video_match = (
                kind == "video"
                and chunk.source_type in VIDEO_SOURCE_TYPES
                and chunk.start_ms == src.get("start_ms")
                and chunk.end_ms == src.get("end_ms")
            )
            pdf_match = (
                kind == "pdf"
                and chunk.source_type == "pdf"
                and chunk.page_no == src.get("page")
            )
            ppt_match = (
                kind == "ppt"
                and chunk.source_type in ("pptx", "ppt")
                and chunk.slide_no == src.get("slide")
            )
            if video_match or pdf_match or ppt_match:
                wanted.append(chunk.chunk_id)
    return wanted


def citation_supported(
    gold_answer: str, chunk_text: str, min_overlap: int = 2
) -> bool:
    """Heuristic citation-support check: gold answer and chunk share content.

    Returns True when at least ``min_overlap`` content tokens co-occur, i.e.
    the cited chunk plausibly contains the claimed information.
    """
    if not gold_answer or not chunk_text:
        return False
    return len(content_tokens(gold_answer) & content_tokens(chunk_text)) >= min_overlap


def should_refuse(
    retrieved: list[RetrievedPassage],
    gold_ids: list[str],
    threshold: float = DEFAULT_THRESHOLD,
) -> bool:
    """Refusal decision for offline eval (no LLM in the loop).

    Refuse when retrieval is empty, when no gold-supporting chunk is gated
    visible (the answer is in the future or nowhere), or when the best score
    is below the relevance threshold.
    """
    if not retrieved:
        return True
    retrieved_ids = {p.chunk_id for p in retrieved}
    if gold_ids and not (retrieved_ids & set(gold_ids)):
        return True
    if not gold_ids:
        # Unanswerable probe: no fixture chunk can support an answer.
        return True
    best = max(p.score for p in retrieved)
    return best < threshold


@dataclass
class ItemResult:
    id: str
    expected: str
    decision: str
    retrieved_ids: list[str] = field(default_factory=list)
    retrieved_gold_ids: list[str] = field(default_factory=list)
    leakage: list[LeakageFinding] = field(default_factory=list)
    latency_ms: float = 0.0
    citation_ok: bool | None = None
    max_score: float = 0.0


def load_course_chunks(path: str | Path) -> tuple[list[Chunk], str, list[str]]:
    """A course exported as JSONL chunks: ``(chunks, course_id, resource_ids)``.

    Every resource in the file is treated as granted, and documents as marked
    done, which is how the lesson page reads a PDF beside the video.
    """
    chunks: list[Chunk] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        meta = {k: row[k] for k in ("kind", "title") if row.get(k)}
        chunks.append(
            Chunk(
                chunk_id=row["chunk_id"],
                resource_id=row["resource_id"],
                course_id=row["course_id"],
                source_type=row["source_type"],
                text=row["text"],
                start_ms=row.get("start_ms"),
                end_ms=row.get("end_ms"),
                page_no=row.get("page_no"),
                metadata=meta,
            )
        )
    if not chunks:
        raise ValueError(f"{path}: no chunks")
    allowed = list(dict.fromkeys(c.resource_id for c in chunks))
    return chunks, chunks[0].course_id, allowed


class CachedEmbedder:
    """Embeds each distinct text once. The harness rebuilds a store per item,
    so a networked embedder would otherwise re-embed the course every time."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self._docs: dict[str, list[float]] = {}
        self._queries: dict[str, list[float]] = {}

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        missing = list(dict.fromkeys(t for t in texts if t not in self._docs))
        if missing:
            for t, v in zip(missing, self._inner.embed_documents(missing)):
                self._docs[t] = v
        return [self._docs[t] for t in texts]

    def embed_query(self, text: str) -> list[float]:
        if text not in self._queries:
            self._queries[text] = self._inner.embed_query(text)
        return self._queries[text]

    def __call__(self, text: str) -> list[float]:
        return self.embed_query(text)


#: Embedders the harness can run with. "hashing" is offline and the default.
EMBEDDERS = ("hashing", "pinecone", "gemini")


def make_embedder(name: str):
    """The named embedder, wrapped in a cache. Network ones need their key."""
    from app.config import settings

    if name == "hashing":
        return HashingEmbedder()
    if name == "pinecone":
        from app.ingestion.embedder import PineconeEmbedder

        if not settings.pinecone_api_key:
            raise SystemExit("--embedder pinecone needs PINECONE_API_KEY")
        return CachedEmbedder(
            PineconeEmbedder(settings.pinecone_embed_model, settings.pinecone_api_key)
        )
    if name == "gemini":
        from app.ingestion.embedder import GeminiEmbedder

        if not settings.embedding_api_key:
            raise SystemExit("--embedder gemini needs EMBEDDING_API_KEY")
        return CachedEmbedder(
            GeminiEmbedder(settings.embedding_model, settings.embedding_api_key)
        )
    raise ValueError(f"unknown embedder {name!r}; choose from {EMBEDDERS}")


def embedder_label(name: str) -> str:
    from app.config import settings

    return {
        "hashing": "HashingEmbedder (offline)",
        "pinecone": f"Pinecone Inference {settings.pinecone_embed_model}",
        "gemini": f"Gemini {settings.embedding_model}",
    }.get(name, name)


def load_golden(path: str | Path) -> list[dict]:
    items: list[dict] = []
    with open(path, encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{lineno}: invalid JSON: {exc}") from exc
    return items


def compute_scoreboard(results: list[ItemResult]) -> dict:
    """Aggregate per-item results into the required scoreboard JSON."""
    answerable = [r for r in results if r.expected == "answer"]
    refusals = [r for r in results if r.expected == "refuse"]
    hits = sum(1 for r in answerable if r.retrieved_gold_ids)
    refused_ok = sum(1 for r in refusals if r.decision == "refuse")
    leaked = sum(1 for r in results if r.leakage)
    latencies = sorted(r.latency_ms for r in results)
    cited = [r for r in answerable if r.citation_ok is not None]
    return {
        "n_total": len(results),
        "n_answer": len(answerable),
        "n_refuse": len(refusals),
        "retrieval_acc": (hits / len(answerable)) if answerable else 0.0,
        "refusal_correctness": (refused_ok / len(refusals)) if refusals else 1.0,
        "leakage_rate": (leaked / len(results)) if results else 0.0,
        "latency_ms_p50": statistics.median(latencies) if latencies else 0.0,
        "latency_ms_p95": (
            latencies[min(len(latencies) - 1, int(0.95 * len(latencies)))]
            if latencies
            else 0.0
        ),
        "citation_acc": (
            sum(1 for r in cited if r.citation_ok) / len(cited) if cited else 0.0
        ),
    }


async def _evaluate_async(
    items: list[dict],
    k: int = DEFAULT_K,
    threshold: float = DEFAULT_THRESHOLD,
    *,
    chunks=None,
    course_id: str = FIXTURE_COURSE_ID,
    allowed: list[str] | None = None,
    embedder=None,
) -> tuple[list[ItemResult], dict]:
    embedder = embedder or HashingEmbedder()
    chunks = chunks if chunks is not None else build_fixture_chunks()
    allowed = allowed if allowed is not None else ALLOWED_RESOURCES
    with tempfile.TemporaryDirectory(
        prefix="eval-chroma-", ignore_cleanup_errors=True  # Windows holds Chroma file locks
    ) as tmp:
        return await _evaluate_with_tmp(
            items, chunks, embedder, tmp, k, threshold, course_id, allowed
        )


async def _evaluate_with_tmp(
    items, chunks, embedder, tmp, k, threshold,
    course_id=FIXTURE_COURSE_ID, allowed=ALLOWED_RESOURCES,
):
    raw_store = ChromaVectorStore(
        path=Path(tmp) / "full", embedder=embedder, collection="eval_full"
    )
    raw_store.add(chunks)
    raw_service = RetrievalService(store=raw_store, embedder=embedder)
    resource_of = {c.chunk_id: c.resource_id for c in chunks}
    text_of = {c.chunk_id: c.text for c in chunks}
    results: list[ItemResult] = []
    gap_items = 0
    for n_stores, item in enumerate(items):
        pause_at_ms = int(item["pause_at_ms"])
        gold_ids = resolve_gold_chunk_ids(item, chunks)
        # Scored pipeline: strict visibility scoping BEFORE ranking, then the
        # production retrieval service ranks the visible pool, then the leakage
        # assertion re-checks the final set. Anything failing the assertion is
        # a harness/implementation bug, never prompt behavior.
        visible = apply_strict_cutoff(chunks, pause_at_ms, allowed)
        vis_store = ChromaVectorStore(
            path=Path(tmp) / f"vis{n_stores}", embedder=embedder, collection="eval_vis"
        )
        vis_store.add(visible)
        service = RetrievalService(store=vis_store, embedder=embedder)
        started = time.perf_counter()
        retrieved = await service.retrieve(
            item["question"],
            course_id,
            allowed,
            pause_at_ms,
            k=k,
        )
        latency_ms = (time.perf_counter() - started) * 1000.0
        # Production-parity diagnostic: raw production retrieval over the full
        # pool, checked under the strict rule. Must be 0 — the store gate and
        # the strict cutoff enforce the same end_ms <= T rule.
        raw_hits = await raw_service.retrieve(
            item["question"],
            course_id,
            allowed,
            pause_at_ms,
            k=k,
        )
        if detect_leakage(raw_hits, pause_at_ms, allowed, resource_of):
            gap_items += 1
        retrieved_ids = [p.chunk_id for p in retrieved]
        retrieved_gold = [cid for cid in retrieved_ids if cid in set(gold_ids)]
        leakage = detect_leakage(retrieved, pause_at_ms, allowed, resource_of)
        decision = (
            "refuse"
            if should_refuse(retrieved, gold_ids, threshold)
            else "answer"
        )
        citation_ok: bool | None = None
        if item.get("expected") == "answer" and retrieved:
            top_text = text_of.get(retrieved[0].chunk_id, "")
            citation_ok = citation_supported(item.get("gold_answer", ""), top_text)
        results.append(
            ItemResult(
                id=str(item.get("id")),
                expected=str(item.get("expected")),
                decision=decision,
                retrieved_ids=retrieved_ids,
                retrieved_gold_ids=retrieved_gold,
                leakage=leakage,
                latency_ms=latency_ms,
                citation_ok=citation_ok,
                max_score=max((p.score for p in retrieved), default=0.0),
            )
        )
    scoreboard = compute_scoreboard(results)
    scoreboard["gap_mode"] = GAP_MODE
    scoreboard["production_gap_items"] = gap_items
    scoreboard["production_gap_rate"] = (gap_items / len(results)) if results else 0.0
    return results, scoreboard


def run_eval(
    golden_path: str | Path | None = None,
    k: int = DEFAULT_K,
    threshold: float = DEFAULT_THRESHOLD,
    chunks_path: str | Path | None = None,
    embedder: str = "hashing",
) -> dict:
    """Load the golden set, run gated retrieval per item, return scoreboard.

    ``chunks_path`` swaps the synthetic fixture course for an exported real
    one; ``embedder`` picks the embedding model (see :data:`EMBEDDERS`).
    """
    path = Path(golden_path) if golden_path else GOLDEN_DEFAULT
    items = load_golden(path)
    course: dict = {}
    if chunks_path:
        chunks, course_id, allowed = load_course_chunks(chunks_path)
        course = {"chunks": chunks, "course_id": course_id, "allowed": allowed}
    results, scoreboard = asyncio.run(
        _evaluate_async(
            items, k=k, threshold=threshold, embedder=make_embedder(embedder), **course
        )
    )
    scoreboard["golden_path"] = str(path)
    scoreboard["chunks_path"] = (
        str(chunks_path) if chunks_path else "fixture (app/eval/fixture.py)"
    )
    scoreboard["embedder"] = embedder_label(embedder)
    scoreboard["k"] = k
    scoreboard["threshold"] = threshold
    scoreboard["items"] = [
        {
            "id": r.id,
            "expected": r.expected,
            "decision": r.decision,
            "retrieved_gold_ids": r.retrieved_gold_ids,
            "leakage": [f.__dict__ for f in r.leakage],
            "latency_ms": round(r.latency_ms, 3),
            "citation_ok": r.citation_ok,
            "max_score": round(r.max_score, 4),
        }
        for r in results
    ]
    return scoreboard


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline eval harness (golden set).")
    parser.add_argument("--golden", default=str(GOLDEN_DEFAULT))
    parser.add_argument("--k", type=int, default=DEFAULT_K)
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument("--out", default="")
    parser.add_argument("--chunks", default="", help="exported course chunks (JSONL)")
    parser.add_argument("--embedder", default="hashing", choices=EMBEDDERS)
    args = parser.parse_args(argv)
    scoreboard = run_eval(
        args.golden, k=args.k, threshold=args.threshold,
        chunks_path=args.chunks or None, embedder=args.embedder,
    )
    rendered = json.dumps(scoreboard, indent=2)
    if args.out:
        Path(args.out).write_text(rendered, encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
