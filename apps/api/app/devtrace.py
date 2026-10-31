"""Live pipeline trace for the developer view.

Every ingest, chat answer and quiz is a *run*: a sequence of step events
("transcribe started", "retrieved 8 passages", "citations validated") that the
web app's developer window polls and animates. The events describe what the
pipeline did and the data it carried -- ids, timestamps, counts, short text
previews -- never keys, prompts in full, or anything from ``.env``.

Recording is a courtesy, like ingest progress: no call in this module may
raise into the pipeline it is observing. Events live in a bounded in-process
ring buffer, so a restart clears them and memory stays flat.
"""

from __future__ import annotations

import contextlib
import contextvars
import itertools
import logging
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass

log = logging.getLogger(__name__)

#: Ring buffer size. A long video ingest emits a few hundred events.
MAX_EVENTS = 4000
#: Longest text preview an event may carry.
PREVIEW_CHARS = 220

_EVENTS: deque[dict] = deque(maxlen=MAX_EVENTS)
_LOCK = threading.Lock()
_SEQ = itertools.count(1)


@dataclass
class Run:
    run_id: str
    flow: str  # "ingest" | "chat" | "quiz" | "grade"
    course_id: str | None


_RUN: contextvars.ContextVar[Run | None] = contextvars.ContextVar("devtrace_run", default=None)


def enabled() -> bool:
    from app.config import settings

    return bool(getattr(settings, "dev_trace", True))


def preview(text: str | None, limit: int = PREVIEW_CHARS) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


def passage_info(p) -> dict:
    """The fields of one retrieved passage worth showing on the diagram."""
    meta = getattr(p, "metadata", None) or {}
    return {
        "chunk_id": getattr(p, "chunk_id", None),
        "resource_id": getattr(p, "resource_id", None),
        "kind": getattr(p, "kind", None) or meta.get("kind") or getattr(p, "source_type", None),
        "start_ms": getattr(p, "start_ms", None),
        "end_ms": getattr(p, "end_ms", None),
        "page_no": getattr(p, "page_no", None),
        "score": _round(getattr(p, "score", None)),
        "text": preview(getattr(p, "text", ""), 160),
    }


def passages_info(passages, limit: int = 12) -> list[dict]:
    return [passage_info(p) for p in list(passages)[:limit]]


def _round(v):
    try:
        return round(float(v), 4)
    except (TypeError, ValueError):
        return None


def where(component: str) -> str:
    """A human label for the place a step runs, from the active settings."""
    try:
        from app.config import settings

        if component == "embed":
            if settings.embedding_provider == "pinecone":
                return f"Pinecone Inference · {settings.pinecone_embed_model}"
            if settings.embedding_api_key:
                return f"Gemini · {settings.embedding_model}"
            return "API · offline hashing embedder"
        if component == "store":
            return "Pinecone vector DB" if settings.vector_store == "pinecone" else "Chroma vector DB"
        if component == "llm":
            return f"Gemini · {settings.llm_model}" if settings.llm_api_key else "API · offline stub LLM"
        if component == "agent":
            from app.chat.models import current_choice

            choice = current_choice()
            if choice.custom:
                return f"{choice.provider} · {choice.model} (own key)"
            return f"OpenRouter · {choice.mode}"
        if component == "asr":
            if settings.groq_api_key:
                return f"Groq · {settings.groq_transcribe_model}"
            return "API · faster-whisper (local)"
        if component == "vision":
            return f"Gemini · {settings.llm_model}" if settings.llm_api_key else "API · no vision model"
    except Exception:  # labels are decoration
        log.debug("devtrace label failed", exc_info=True)
    return "API · FastAPI on Render"


API = "API · FastAPI on Render"


def _append(event: dict) -> None:
    with _LOCK:
        event["seq"] = next(_SEQ)
        _EVENTS.append(event)


def emit(
    step: str,
    phase: str = "info",
    *,
    title: str = "",
    at: str | None = None,
    data: dict | None = None,
    ms: float | None = None,
    run: Run | None = None,
) -> None:
    """Record one step event on the current run. Never raises."""
    try:
        if not enabled():
            return
        run = run or _RUN.get()
        if run is None:
            return
        _append({
            "ts": time.time(),
            "run_id": run.run_id,
            "flow": run.flow,
            "course_id": run.course_id,
            "step": step,
            "phase": phase,
            "title": title,
            "where": at or API,
            "data": data or {},
            "ms": round(ms, 1) if ms is not None else None,
        })
    except Exception:
        log.debug("devtrace emit failed", exc_info=True)


def start_run(flow: str, course_id: str | None, *, run_id: str | None = None,
              title: str = "", data: dict | None = None) -> contextvars.Token:
    """Make a new run current in this context and emit its opening event."""
    run = Run(run_id=run_id or f"{flow}_{uuid.uuid4().hex[:10]}", flow=flow, course_id=course_id)
    token = _RUN.set(run)
    emit("request", "start", title=title, data=data)
    return token


def end_run(token: contextvars.Token | None, phase: str = "end", *,
            title: str = "", data: dict | None = None) -> None:
    emit("respond", phase, title=title, data=data)
    if token is not None:
        with contextlib.suppress(Exception):
            _RUN.reset(token)


def current_run() -> Run | None:
    return _RUN.get()


@contextlib.contextmanager
def span(step: str, *, title: str = "", at: str | None = None, data: dict | None = None):
    """Emit ``start`` now and ``end`` (with elapsed ms) on exit.

    Yields a dict: whatever the body puts in it rides on the ``end`` event.
    An exception emits ``error`` and propagates unchanged.
    """
    out: dict = {}
    emit(step, "start", title=title, at=at, data=data)
    t0 = time.perf_counter()
    try:
        yield out
    except BaseException as exc:
        emit(step, "error", title=title, at=at,
             data={**out, "error": type(exc).__name__},
             ms=(time.perf_counter() - t0) * 1000)
        raise
    emit(step, "end", title=title, at=at, data=out, ms=(time.perf_counter() - t0) * 1000)


def events_after(after: int = 0, course_id: str | None = None, limit: int = 500) -> list[dict]:
    with _LOCK:
        items = [e for e in _EVENTS if e["seq"] > after]
    if course_id:
        items = [e for e in items if e.get("course_id") in (course_id, None)]
    return items[:limit]


def latest_seq() -> int:
    with _LOCK:
        return _EVENTS[-1]["seq"] if _EVENTS else 0


def clear() -> None:
    with _LOCK:
        _EVENTS.clear()


def export_runs(run_id: str | None = None, flow: str | None = None) -> dict:
    """Every buffered event, grouped by run, for download from the developer view.

    Each run carries a summary (flow, start/end, duration, errors, the
    question and answer previews) ahead of its events, so a failed run can be
    read top to bottom. Same data as the live trace: previews and ids only.
    """
    with _LOCK:
        items = list(_EVENTS)
    if run_id:
        items = [e for e in items if e.get("run_id") == run_id]
    if flow:
        items = [e for e in items if e.get("flow") == flow]
    runs: dict[str, dict] = {}
    for e in items:
        rid = e.get("run_id") or "none"
        r = runs.setdefault(rid, {"run_id": rid, "flow": e.get("flow"), "course_id": e.get("course_id"),
                                  "events": []})
        r["events"].append(e)
    out = []
    for r in runs.values():
        evs = r["events"]
        t0, t1 = evs[0].get("ts"), evs[-1].get("ts")
        errors = [
            {"step": e.get("step"), "title": e.get("title"), "data": e.get("data")}
            for e in evs if e.get("phase") == "error"
        ]
        first_data = next((e.get("data") or {} for e in evs if e.get("step") == "request"), {})
        last_data = next((e.get("data") or {} for e in reversed(evs) if e.get("step") == "respond"), {})
        r["summary"] = {
            "started": t0,
            "ended": t1,
            "duration_ms": round((t1 - t0) * 1000) if isinstance(t0, (int, float)) and isinstance(t1, (int, float)) else None,
            "steps": len(evs),
            "status": "error" if errors else ("done" if evs[-1].get("step") == "respond" else "incomplete"),
            "errors": errors,
            "question": first_data.get("question"),
            "answer": last_data.get("answer"),
            "path": [e.get("title") for e in evs if e.get("step") == "route" and e.get("phase") == "end"],
        }
        out.append({k: r[k] for k in ("run_id", "flow", "course_id", "summary", "events")})
    return {
        "exported_at": time.time(),
        "buffer_size": MAX_EVENTS,
        "run_count": len(out),
        "runs": out,
    }
