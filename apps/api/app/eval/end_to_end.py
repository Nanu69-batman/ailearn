"""End-to-end evaluation: the real answer pipeline over a real course.

Where ``app.eval.harness`` scores retrieval only, this runs every golden
question through :class:`app.chat.service.ChatService` -- intent routing,
the strict-T gate, the LLM, citation validation and refusals -- exactly as
``POST /chat`` does, and scores what the learner would see:

* **spoiler-safe**: no citation points at video content that ends after the
  pause (``end_ms > T``) or at a material outside the learner's scope. Must be
  every question.
* **outcome**: ``grounded`` (answered with validated citations),
  ``supplemental`` (a general-knowledge answer labelled "Not from the video",
  no citations), ``conversational``, or ``refused``.
* **cited a gold location**: for answerable questions, whether at least one
  citation is a piece that states the answer.
* **withheld**: for questions that must not be answered from the lesson (the
  answer comes after the pause, or the course never covers it), whether the
  reply avoided revealing the withheld fact. A spoiler question lists the
  fact's tell-tale strings in ``withheld_terms``; saying "the lesson has not
  got there yet" while citing earlier material is the correct behaviour.

Each question gets a fresh session, so earlier questions cannot steer later
ones. The PDF is marked done, as on the lesson page, so it is readable in full
beside the video.

``--rescore <results.json>`` re-scores saved answers against the current
golden file without calling any model.

Usage (from ``apps/api``; needs ``LLM_API_KEY`` and ``PINECONE_API_KEY``)::

    python -m app.eval.end_to_end \\
        --golden ../../submission/eval/golden-partial-derivatives.jsonl \\
        --chunks ../../submission/eval/partial-derivatives-chunks.jsonl \\
        --out ../../submission/eval-results/partial-derivatives-end-to-end.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import tempfile
import time
from pathlib import Path

from app.eval.harness import (
    EMBEDDERS,
    embedder_label,
    load_course_chunks,
    load_golden,
    make_embedder,
    resolve_gold_chunk_ids,
)

#: Seconds between questions, to stay inside free-tier request limits.
DEFAULT_PAUSE_S = 4.0


def classify(resp) -> str:
    if resp.refused:
        return "refused"
    if resp.mode in ("supplemental", "conversational"):
        return resp.mode
    return "grounded" if resp.citations else "uncited"


def _norm(text: str) -> str:
    return "".join(ch for ch in (text or "").lower() if ch not in " $\\{}\n\t")


def revealed(answer: str | None, terms) -> list[str]:
    """The withheld strings that appear in ``answer`` (spacing and LaTeX ignored)."""
    body = _norm(answer or "")
    return [t for t in terms or () if _norm(t) and _norm(t) in body]


def item_verdict(expected: str, outcome: str, gold_cited: bool, leaked_terms=()) -> str:
    """A short label for the result table."""
    if expected == "answer":
        if outcome == "grounded":
            return "correct" if gold_cited else "answered, other source"
        if outcome == "refused":
            return "over-refused"
        return "answered without the lesson"
    # Must not be answered from the lesson: after the pause, or not covered.
    if leaked_terms:
        return "revealed withheld content"
    return "correct"


async def _run(items, chunks, course_id, allowed, embedder, *, pause_s, use_agent):
    from app.chat.llm import get_llm_provider
    from app.chat.service import ChatService
    from app.chat.sessions import InMemoryMessageStore, InMemorySessionStore
    from app.retrieval.store import ChromaVectorStore

    by_id = {c.chunk_id: c for c in chunks}
    video_ids = [r for r in allowed if any(c.resource_id == r and c.source_type == "video" for c in chunks)]
    focus = video_ids[0] if video_ids else None
    done = [r for r in allowed if r != focus]
    agent = None
    if use_agent:
        from app.chat.agent import get_agent

        agent = get_agent()
    llm = get_llm_provider()
    results = []
    with tempfile.TemporaryDirectory(prefix="e2e-chroma-", ignore_cleanup_errors=True) as tmp:
        store = ChromaVectorStore(path=Path(tmp) / "course", embedder=embedder, collection="e2e")
        store.add(chunks)
        for n, item in enumerate(items):
            if n and pause_s:
                await asyncio.sleep(pause_s)
            t_ms = int(item["pause_at_ms"])
            sessions = InMemorySessionStore()
            session = sessions.create(
                learner_id="eval", course_id=course_id, resource_id=focus or allowed[0],
                lesson_id=None, resource_scope=list(allowed), current_time_ms=t_ms,
            )
            service = ChatService(
                store=store, embedder=embedder, llm=llm, sessions=sessions,
                messages=InMemoryMessageStore(), agent=agent,
            )
            started = time.perf_counter()
            error = None
            try:
                resp = await service.chat(
                    session_id=session.id, current_time_ms=t_ms, message=item["question"],
                    focus_resource_id=focus, completed_resource_ids=done,
                )
            except Exception as exc:  # noqa: BLE001 -- recorded per item, the run goes on
                resp, error = None, f"{type(exc).__name__}: {exc}"
            latency_s = time.perf_counter() - started
            gold = set(resolve_gold_chunk_ids(item, chunks))
            cites = []
            leaks = []
            if resp is not None:
                for c in resp.citations:
                    chunk = by_id.get(c.chunk_id)
                    rid = chunk.resource_id if chunk else None
                    cites.append({
                        "chunk_id": c.chunk_id, "resource_id": rid, "start_ms": c.start_ms,
                        "end_ms": c.end_ms, "page_no": c.page_no, "gold": c.chunk_id in gold,
                    })
                    late = (
                        chunk is not None and chunk.source_type == "video"
                        and rid not in done and (c.end_ms is None or c.end_ms > t_ms)
                    )
                    if late or rid not in allowed:
                        leaks.append(c.chunk_id)
            outcome = classify(resp) if resp is not None else "error"
            gold_cited = any(c["gold"] for c in cites)
            leaked = revealed(resp.answer if resp is not None else None, item.get("withheld_terms"))
            results.append({
                "id": item["id"], "type": item.get("type"), "expected": item["expected"],
                "pause_at_ms": t_ms, "question": item["question"],
                "outcome": outcome,
                "verdict": item_verdict(item["expected"], outcome, gold_cited, leaked) if resp else "error",
                "gold_cited": gold_cited, "spoiler_leaks": leaks, "revealed_terms": leaked,
                "citations": cites,
                "answer": (resp.answer if resp is not None else None), "error": error,
                "latency_s": round(latency_s, 2),
            })
            print(f"{item['id']:8} {outcome:13} {results[-1]['verdict']:28} {latency_s:6.1f}s", flush=True)
    return results


def scoreboard(results: list[dict]) -> dict:
    answer = [r for r in results if r["expected"] == "answer"]
    refuse = [r for r in results if r["expected"] == "refuse"]
    lat = sorted(r["latency_s"] for r in results if r["outcome"] != "error")

    def share(rows, pred):
        return round(sum(1 for r in rows if pred(r)) / len(rows), 4) if rows else 0.0

    return {
        "n_total": len(results), "n_answer": len(answer), "n_refuse": len(refuse),
        "spoiler_safe_rate": share(results, lambda r: not r["spoiler_leaks"]),
        "answered_with_gold_citation": share(answer, lambda r: r["outcome"] == "grounded" and r["gold_cited"]),
        "answered_grounded": share(answer, lambda r: r["outcome"] == "grounded"),
        "over_refused": share(answer, lambda r: r["outcome"] == "refused"),
        "withheld_correctly": share(refuse, lambda r: r["verdict"] == "correct"),
        "errors": sum(1 for r in results if r["outcome"] == "error"),
        "latency_s_p50": statistics.median(lat) if lat else 0.0,
        "latency_s_p95": lat[min(len(lat) - 1, int(0.95 * len(lat)))] if lat else 0.0,
        "outcomes": {o: sum(1 for r in results if r["outcome"] == o) for o in sorted({r["outcome"] for r in results})},
    }


def rescore(results: list[dict], items: list[dict], chunks) -> list[dict]:
    """Recompute gold hits and verdicts for saved answers (no model calls)."""
    by_item = {i["id"]: i for i in items}
    for r in results:
        item = by_item[r["id"]]
        gold = set(resolve_gold_chunk_ids(item, chunks))
        for c in r["citations"]:
            c["gold"] = c["chunk_id"] in gold
        r["gold_cited"] = any(c["gold"] for c in r["citations"])
        r["revealed_terms"] = revealed(r.get("answer"), item.get("withheld_terms"))
        if r["outcome"] != "error":
            r["verdict"] = item_verdict(r["expected"], r["outcome"], r["gold_cited"], r["revealed_terms"])
    return results


def main(argv: list[str] | None = None) -> int:
    from app.config import settings

    parser = argparse.ArgumentParser(description="End-to-end eval through ChatService.")
    parser.add_argument("--golden", required=True)
    parser.add_argument("--chunks", required=True)
    parser.add_argument("--rescore", default="", help="re-score a saved results JSON, no model calls")
    parser.add_argument("--embedder", default="pinecone", choices=EMBEDDERS)
    parser.add_argument("--agent", action="store_true", help="let the OpenRouter agent answer first")
    parser.add_argument("--pause", type=float, default=DEFAULT_PAUSE_S)
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)
    if args.rescore:
        saved = json.loads(Path(args.rescore).read_text(encoding="utf-8"))
        chunks, _, _ = load_course_chunks(args.chunks)
        results = rescore(saved["items"], load_golden(args.golden), chunks)
        board = {k: v for k, v in saved.items() if k != "items"}
        board.update(scoreboard(results))
        board.pop("refuse_items_not_answered_from_lesson", None)
        board["items"] = results
        out = Path(args.out or args.rescore)
        out.write_text(json.dumps(board, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps({k: v for k, v in board.items() if k != "items"}, indent=2))
        return 0
    if not settings.llm_api_key:
        raise SystemExit("LLM_API_KEY is not set: the end-to-end eval needs the real LLM")
    # Measure real calls, not answers cached from an earlier run.
    settings.cache_enabled = False
    chunks, course_id, allowed = load_course_chunks(args.chunks)
    items = load_golden(args.golden)
    results = asyncio.run(_run(
        items, chunks, course_id, allowed, make_embedder(args.embedder),
        pause_s=args.pause, use_agent=args.agent,
    ))
    board = scoreboard(results)
    board.update({
        "golden_path": args.golden, "chunks_path": args.chunks,
        "embedder": embedder_label(args.embedder),
        "llm": settings.llm_model,
        "agent": settings.openrouter_model if args.agent else None,
        "pipeline": "agent first, classic fallback" if args.agent else "classic (no agent)",
        "items": results,
    })
    rendered = json.dumps(board, indent=2, ensure_ascii=False)
    if args.out:
        Path(args.out).write_text(rendered, encoding="utf-8")
    print(json.dumps({k: v for k, v in board.items() if k != "items"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
