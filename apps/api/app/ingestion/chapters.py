"""Automatic chapters for every indexed resource.

    GET /resources/{resource_id}/chapters
        -> { resource_id, status: "ready"|"pending"|"unavailable", chapters: [...] }

A chapter is a short topic name over a contiguous span of the resource: a
time range for a video (``start_ms``/``end_ms``), a page range for a PDF
(``page_start``/``page_end``), a slide range for a PPTX
(``slide_start``/``slide_end``). Every field of the other kinds is ``None``.

Chapters are a CATALOGUE feature. They are computed from the chunks the
ingest already indexed, read by an exact ``resource_id`` equality filter, and
they never feed the tutor's retrieval: nothing here touches ``build_where``,
the strict ``end_ms <= T`` gate or citation validation. A chapter list that
names the end of a lecture is navigation the learner chose to open, not
evidence handed to an answer.

Generation is one LLM call over a compact, ordered digest of the resource. Its
JSON is validated hard (boundaries snapped to real timestamps/pages, sorted,
gaps and overlaps repaired, junk titles dropped); anything unusable -- and the
offline stub provider -- falls through to a deterministic split, which is
still a ``ready`` result.

Storage is one JSON file per resource under :func:`chapters_dir`. A partly
processed (stream-mode) video stores the ``through_ms`` it was built from and
is rebuilt once the frontier has moved on by more than
:data:`REGEN_ADVANCE_MS`, or the video is fully processed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

#: Env override for the storage directory (tests, alternate deployments).
CHAPTERS_DIR_ENV = "CHAPTERS_DIR"

#: Module-level override; wins over the env var. Tests point it at tmp_path.
CHAPTERS_DIR: Path | None = None

#: A partial video is rebuilt once its frontier has advanced this far.
REGEN_ADVANCE_MS = 120_000

#: Digest cap fed to the LLM. Longer digests are thinned evenly, not cut off,
#: so the end of a lecture is still represented.
DIGEST_CHAR_CAP = 20_000

#: Per-line cap inside the digest; one long chunk must not crowd out the rest.
_LINE_CHAR_CAP = 320

#: Fallback span for videos.
FALLBACK_SPAN_MS = 5 * 60_000

#: A video chapter shorter than this is merged into its neighbour.
MIN_VIDEO_CHAPTER_MS = 30_000

MAX_VIDEO_CHAPTERS = 12
MAX_DOC_CHAPTERS = 10

#: Whether a successful ingest schedules generation on its own. ``None`` means
#: "on, except under pytest": the suite swaps the shared store per test and
#: must never write chapter files into the developer's ``data/`` or spend an
#: LLM call per ingest test. Tests that want it set this to ``True``.
AUTO_GENERATE: bool | None = None

STATUS_READY = "ready"
STATUS_PENDING = "pending"
STATUS_UNAVAILABLE = "unavailable"


def _start_thread(fn: Callable[[], None]) -> None:
    threading.Thread(target=fn, name="chapters", daemon=True).start()


#: How a background generation is started. Tests replace it to capture the
#: callable and run it synchronously.
SPAWN: Callable[[Callable[[], None]], None] = _start_thread

_LOCK = threading.Lock()
_INFLIGHT: set[str] = set()

_SAFE_ID = re.compile(r"[A-Za-z0-9_\-]{1,128}")


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def chapters_dir() -> Path:
    """Where chapter files live: module override, env var, then ``data/chapters``."""
    if CHAPTERS_DIR is not None:
        return Path(CHAPTERS_DIR)
    override = os.environ.get(CHAPTERS_DIR_ENV)
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent.parent / "data" / "chapters"


def _path_for(resource_id: str) -> Path | None:
    # The id names a file, so only the id grammar is ever accepted.
    if not _SAFE_ID.fullmatch(resource_id or ""):
        return None
    return chapters_dir() / f"{resource_id}.json"


def load_record(resource_id: str) -> dict | None:
    """The stored record for ``resource_id``, or ``None`` (missing/corrupt)."""
    path = _path_for(resource_id)
    if path is None or not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log.warning("unreadable chapters file %s; regenerating", path)
        return None
    return data if isinstance(data, dict) and isinstance(data.get("chapters"), list) else None


def save_record(record: dict) -> None:
    path = _path_for(str(record.get("resource_id") or ""))
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write-then-rename so a reader polling mid-write never sees half a file.
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def delete_chapters(resource_id: str) -> None:
    """Remove a resource's chapters (resource deletion). Missing is fine."""
    path = _path_for(resource_id)
    if path is not None:
        path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Reading the indexed resource
# ---------------------------------------------------------------------------


@dataclass
class _Unit:
    """One ordered piece of evidence: a timed chunk, or a page/slide's text."""

    text: str
    start_ms: int | None = None
    end_ms: int | None = None
    number: int | None = None  # page or slide, 1-based


@dataclass
class ResourceView:
    """What chapter generation needs to know about one resource."""

    resource_id: str
    source_type: str
    #: "video", "page" (pdf), "slide" (pptx) or "none" (e.g. an image).
    axis: str
    units: list[_Unit]
    #: Video: last indexed end_ms. Docs: unused.
    cover_end_ms: int = 0
    #: Video: processing frontier the chapters reflect. Docs: unit count.
    through: int = 0
    #: Video: fully processed (or not windowed at all).
    complete: bool = True


def _gated_types() -> frozenset[str]:
    from app.retrieval.store import TIME_GATED_SOURCE_TYPES

    return TIME_GATED_SOURCE_TYPES


def read_resource(resource_id: str, store=None) -> ResourceView | None:
    """Read ``resource_id``'s chunks by exact id. ``None`` when nothing is indexed."""
    if store is None:
        from app.retrieval.registry import get_shared_store

        store = get_shared_store()
    try:
        got = store._collection.get(
            where={"resource_id": {"$eq": resource_id}},
            include=["documents", "metadatas"],
        )
    except Exception as exc:  # noqa: BLE001 - a catalog miss must not 500
        log.warning("chapters: reading %s failed: %s", resource_id, exc)
        return None
    docs = got.get("documents") or []
    metas = got.get("metadatas") or []
    passages = []
    frontier = None
    duration = None
    for doc, meta in zip(docs, metas):
        meta = meta or {}
        if meta.get("resource_id") != resource_id:
            continue
        # Same MAX-over-chunks fold as ``processing_frontier``.
        v = meta.get("processed_through_ms")
        if isinstance(v, int) and (frontier is None or v > frontier):
            frontier = v
        v = meta.get("duration_ms")
        if isinstance(v, int) and (duration is None or v > duration):
            duration = v
        # _to_passage drops progress markers (no chunk_id) and normalises the
        # -1 sentinels, so this reads exactly what the catalogue reads.
        passage = store._to_passage(doc, meta, 0.0)
        if passage is not None and (passage.text or "").strip():
            passages.append(passage)
    if not passages:
        return None
    source_type = passages[0].source_type
    if source_type in _gated_types():
        return _video_view(resource_id, source_type, passages, frontier, duration)
    if source_type == "pptx" or any(p.slide_no is not None for p in passages):
        return _doc_view(resource_id, source_type, passages, "slide")
    if source_type == "pdf" or any(p.page_no is not None for p in passages):
        return _doc_view(resource_id, source_type, passages, "page")
    return ResourceView(resource_id, source_type, "none", [])


def _video_view(resource_id, source_type, passages, frontier, duration) -> ResourceView:
    timed = [p for p in passages if p.start_ms is not None and p.end_ms is not None]
    if not timed:
        return ResourceView(resource_id, source_type, "none", [])
    speech = [p for p in timed if (p.kind or "") != "visual"]
    # Speech carries the topic; frame descriptions are the fallback for a
    # silent screencast.
    chosen = speech or timed
    chosen.sort(key=lambda p: (p.start_ms, p.end_ms, p.chunk_id))
    units = [_Unit(text=p.text.strip(), start_ms=p.start_ms, end_ms=p.end_ms) for p in chosen]
    cover_end = max(p.end_ms for p in timed)
    through = frontier if frontier is not None else cover_end
    complete = frontier is None or duration is None or frontier >= duration
    return ResourceView(
        resource_id, source_type, "video", units,
        cover_end_ms=cover_end, through=through, complete=complete,
    )


def _doc_view(resource_id, source_type, passages, axis: str) -> ResourceView:
    by_no: dict[int, list[str]] = {}
    for p in passages:
        no = p.slide_no if axis == "slide" else p.page_no
        if no is None:
            continue
        by_no.setdefault(int(no), []).append(p.text.strip())
    if not by_no:
        # No page numbers at all: one chapter, no range (nothing to cite).
        text = " ".join(p.text.strip() for p in passages)
        return ResourceView(resource_id, source_type, "none", [_Unit(text=text)])
    units = [_Unit(text="\n".join(by_no[n]), number=n) for n in sorted(by_no)]
    return ResourceView(resource_id, source_type, axis, units, through=len(units))


# ---------------------------------------------------------------------------
# Chapter shapes
# ---------------------------------------------------------------------------


def _chapter(title: str, **fields) -> dict:
    out = {
        "title": title,
        "start_ms": None, "end_ms": None,
        "page_start": None, "page_end": None,
        "slide_start": None, "slide_end": None,
    }
    out.update(fields)
    return out


def _video_chapters(view: ResourceView, starts: list[int], titles: list[str]) -> list[dict]:
    ends = starts[1:] + [view.cover_end_ms]
    return [_chapter(t, start_ms=s, end_ms=e) for s, e, t in zip(starts, ends, titles)]


def _doc_chapters(view: ResourceView, starts: list[int], titles: list[str]) -> list[dict]:
    last = view.units[-1].number
    numbers = [u.number for u in view.units]
    out = []
    for i, (s, t) in enumerate(zip(starts, titles)):
        if i + 1 < len(starts):
            # The last real page before the next chapter's start, so a gap in
            # page numbering never produces an end that points at nothing.
            nxt = starts[i + 1]
            e = max(n for n in numbers if n < nxt)
        else:
            e = last
        key = "slide" if view.axis == "slide" else "page"
        out.append(_chapter(t, **{f"{key}_start": s, f"{key}_end": e}))
    return out


# ---------------------------------------------------------------------------
# Titles
# ---------------------------------------------------------------------------

_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'\-]*|[^\W\d_][\w'\-]*", re.UNICODE)


def _first_words(text: str, n: int = 6) -> str:
    words = _WORD.findall(text or "")
    return " ".join(words[:n])


def clean_title(raw) -> str | None:
    """A usable chapter title, or ``None``.

    Titles are topic names. Anything that reads like a worked result
    ("dy comes out to 2xy") -- an equation, or a long sentence -- is rejected
    rather than shown as navigation.
    """
    if not isinstance(raw, str):
        return None
    title = re.sub(r"\s+", " ", raw).strip().strip("\"'`*#").strip()
    title = re.sub(r"^(chapter|part|section)\s*\d+\s*[:.\-]\s*", "", title, flags=re.IGNORECASE)
    title = title.rstrip(".;,:")
    if not title or "=" in title or len(title) > 80:
        return None
    if len(title.split()) > 9:
        return None
    if re.search(r"\b(comes out|equals|is equal to|the answer)\b", title, re.IGNORECASE):
        return None
    return title


# ---------------------------------------------------------------------------
# Deterministic fallback
# ---------------------------------------------------------------------------


def fallback_chapters(view: ResourceView) -> list[dict]:
    """Chapters without an LLM: equal video spans, or doc heading groups."""
    if view.axis == "video":
        end = max(view.cover_end_ms, 1)
        n = max(1, min(MAX_VIDEO_CHAPTERS, round(end / FALLBACK_SPAN_MS)))
        span = end / n
        starts = [round(i * span) for i in range(n)]
        titles = []
        for i, s in enumerate(starts):
            e = starts[i + 1] if i + 1 < n else end
            text = " ".join(
                u.text for u in view.units if u.start_ms is not None and s <= u.start_ms < e
            )
            words = _first_words(text)
            titles.append(f"Part {i + 1}: {words}" if words else f"Part {i + 1}")
        return _video_chapters(view, starts, titles)
    if view.axis in ("page", "slide"):
        return _doc_chapters(view, *_doc_fallback_groups(view))
    if view.units:
        words = _first_words(view.units[0].text)
        return [_chapter(words or "Overview")]
    return []


def _heading(text: str) -> str:
    for line in (text or "").splitlines():
        line = line.strip()
        if len(_WORD.findall(line)) >= 1:
            return _first_words(line, 7)
    return ""


def _doc_fallback_groups(view: ResourceView) -> tuple[list[int], list[str]]:
    label = "Slides" if view.axis == "slide" else "Pages"
    units = view.units
    # Group consecutive pages that share a heading (a slide title carried
    # over several slides, a running header).
    groups: list[tuple[int, str]] = []
    for u in units:
        head = _heading(u.text)
        if groups and head and head.lower() == groups[-1][1].lower():
            continue
        groups.append((u.number, head))
    if len(groups) <= MAX_DOC_CHAPTERS:
        starts = [g[0] for g in groups]
        titles = [g[1] or f"{label[:-1]} {g[0]}" for g in groups]
        return starts, titles
    # Too many headings: fixed-size groups, titled by their first heading.
    size = math.ceil(len(units) / MAX_DOC_CHAPTERS)
    starts, titles = [], []
    for i in range(0, len(units), size):
        group = units[i:i + size]
        head = _heading(group[0].text)
        starts.append(group[0].number)
        titles.append(head or f"{label} {group[0].number}-{group[-1].number}")
    return starts, titles


# ---------------------------------------------------------------------------
# LLM generation + validation
# ---------------------------------------------------------------------------

_SYSTEM = (
    "You divide course material into chapters for a table of contents. "
    "Reply with JSON only, no prose and no code fences."
)


def _fmt_ms(ms: int) -> str:
    s = ms // 1000
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def _thin(lines: list[str], cap: int) -> list[str]:
    total = sum(len(x) + 1 for x in lines)
    if total <= cap or not lines:
        return lines
    keep = max(1, int(len(lines) * cap / total))
    step = len(lines) / keep
    return [lines[int(i * step)] for i in range(keep)]


def video_bounds(view: ResourceView) -> tuple[int, int]:
    """(min, max) chapters for a video of this length (~1 per 3-8 minutes)."""
    minutes = view.cover_end_ms / 60_000
    hi = max(1, min(MAX_VIDEO_CHAPTERS, int(minutes // 3)))
    lo = 1 if minutes < 6 else max(2, min(hi, int(minutes // 8)))
    return lo, max(lo, hi)


def build_prompt(view: ResourceView) -> str:
    if view.axis == "video":
        lines = [f"[{u.start_ms // 1000}s] {u.text[:_LINE_CHAR_CAP]}" for u in view.units]
        lo, hi = video_bounds(view)
        lines = _thin(lines, DIGEST_CHAR_CAP)
        return (
            f"Below is a timestamped transcript of a {_fmt_ms(view.cover_end_ms)} lecture "
            "video; each line starts with its start time in seconds.\n"
            f"Split it into {lo}-{hi} chapters at the points where the topic changes.\n"
            "Rules: each chapter's start_s must be one of the given [Ns] timestamps; the "
            "first chapter starts at 0. Each title is a short topic name of 2-7 words "
            "(e.g. \"Partial Derivatives\", \"The Chain Rule\"); never a result, answer, "
            "equation or full sentence.\n"
            'Return: {"chapters": [{"title": "...", "start_s": 0}, ...]}\n\n'
            + "\n".join(lines)
        )
    noun = "slide" if view.axis == "slide" else "page"
    lines = [
        f"[{noun} {u.number}] {re.sub(r'\s+', ' ', u.text)[:_LINE_CHAR_CAP]}"
        for u in view.units
    ]
    lines = _thin(lines, DIGEST_CHAR_CAP)
    hi = min(MAX_DOC_CHAPTERS, len(view.units))
    return (
        f"Below is the text of a {len(view.units)}-{noun} course document, one line per "
        f"{noun}.\n"
        f"Group consecutive {noun}s into 1-{hi} chapters by topic.\n"
        f"Rules: each chapter's start must be one of the given {noun} numbers; the first "
        "chapter starts at the first one. Each title is a short topic name of 2-7 words; "
        "never a result, answer, equation or full sentence.\n"
        f'Return: {{"chapters": [{{"title": "...", "start": {view.units[0].number}}}, ...]}}\n\n'
        + "\n".join(lines)
    )


def _parse_json(text: str):
    if not isinstance(text, str):
        return None
    body = text.strip()
    body = re.sub(r"^```(?:json)?\s*|\s*```$", "", body)
    for opener, closer in (("{", "}"), ("[", "]")):
        i, j = body.find(opener), body.rfind(closer)
        if i != -1 and j > i:
            try:
                return json.loads(body[i:j + 1])
            except ValueError:
                continue
    return None


def _as_number(raw) -> float | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)) and math.isfinite(raw):
        return float(raw)
    if isinstance(raw, str):
        m = re.fullmatch(r"\s*(?:(\d+):)?(\d+):(\d{1,2})\s*", raw)
        if m:
            h, mi, s = int(m.group(1) or 0), int(m.group(2)), int(m.group(3))
            return float(h * 3600 + mi * 60 + s)
        try:
            return float(raw)
        except ValueError:
            return None
    return None


def validate_llm_chapters(view: ResourceView, payload) -> list[dict] | None:
    """Turn an LLM payload into valid chapters, or ``None`` if it is junk.

    Boundaries are snapped to real unit starts (timestamps or page numbers),
    clamped to the indexed range, sorted and de-duplicated; ends are derived
    from the next start, so the result is contiguous and non-overlapping by
    construction. Entries with an unusable title are dropped (their span joins
    the previous chapter).
    """
    if isinstance(payload, dict):
        payload = payload.get("chapters")
    if not isinstance(payload, list) or not view.units:
        return None
    video = view.axis == "video"
    candidates = [u.start_ms for u in view.units] if video else [u.number for u in view.units]
    picked: dict[int, str] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        title = clean_title(item.get("title"))
        if title is None:
            continue
        if video:
            raw = _as_number(item.get("start_s", item.get("start")))
            if raw is None and "start_ms" in item:
                ms = _as_number(item.get("start_ms"))
                raw = None if ms is None else ms / 1000
            if raw is None:
                continue
            target = min(max(raw * 1000, 0), view.cover_end_ms)
        else:
            raw = _as_number(item.get("start", item.get("page_start", item.get("slide_start"))))
            if raw is None:
                continue
            target = raw
        snapped = min(candidates, key=lambda c: (abs(c - target), c))
        picked.setdefault(int(snapped), title)
    if not picked:
        return None
    starts = sorted(picked)
    titles = [picked[s] for s in starts]
    # The first chapter always opens the resource.
    starts[0] = 0 if video else candidates[0]
    if video:
        starts, titles = _merge_short(starts, titles, view.cover_end_ms)
        lo, hi = video_bounds(view)
        while len(starts) > hi:
            starts, titles = _merge_shortest(starts, titles, view.cover_end_ms)
        if len(starts) < lo:
            return None
        return _video_chapters(view, starts, titles)
    while len(starts) > MAX_DOC_CHAPTERS:
        starts, titles = _merge_shortest(starts, titles, candidates[-1] + 1)
    return _doc_chapters(view, starts, titles)


def _merge_short(starts, titles, end) -> tuple[list[int], list[str]]:
    while len(starts) > 1:
        ends = starts[1:] + [end]
        short = [i for i in range(len(starts)) if ends[i] - starts[i] < MIN_VIDEO_CHAPTER_MS]
        if not short:
            break
        i = short[0]
        # Fold a too-short chapter into its predecessor (or the first into its
        # successor, keeping the earlier title).
        j = i if i > 0 else 1
        starts = starts[:j] + starts[j + 1:]
        titles = titles[:j] + titles[j + 1:]
    return starts, titles


def _merge_shortest(starts, titles, end) -> tuple[list[int], list[str]]:
    ends = starts[1:] + [end]
    i = min(range(len(starts)), key=lambda k: ends[k] - starts[k])
    j = i if i > 0 else 1
    return starts[:j] + starts[j + 1:], titles[:j] + titles[j + 1:]


def _default_llm_complete(system: str, user_prompt: str) -> str | None:
    """One completion via the chat provider, or ``None`` offline/stubbed."""
    from app.chat.llm import DeterministicStubLLM, get_llm_provider

    provider = get_llm_provider()
    if isinstance(provider, DeterministicStubLLM):
        return None
    result = asyncio.run(
        provider.complete(system=system, user_prompt=user_prompt, passages=[], mode="chapters")
    )
    return result.text


#: Swappable in tests; returns raw model text or ``None``.
LLM_COMPLETE: Callable[[str, str], str | None] = _default_llm_complete


def generate(view: ResourceView) -> tuple[list[dict], str]:
    """Chapters for ``view`` and how they were made (``"llm"``/``"fallback"``)."""
    if view.axis in ("video", "page", "slide") and view.units:
        try:
            text = LLM_COMPLETE(_SYSTEM, build_prompt(view))
        except Exception as exc:  # noqa: BLE001 - quota, network, SDK: all fall back
            log.warning("chapters LLM call failed for %s: %s", view.resource_id, exc)
            text = None
        if text:
            chapters = validate_llm_chapters(view, _parse_json(text))
            if chapters:
                return chapters, "llm"
            log.info("chapters: unusable LLM output for %s; using fallback", view.resource_id)
    return fallback_chapters(view), "fallback"


# ---------------------------------------------------------------------------
# Freshness, scheduling, the GET payload
# ---------------------------------------------------------------------------


def is_stale(record: dict | None, view: ResourceView) -> bool:
    if record is None:
        return True
    if record.get("axis") != view.axis:
        return True
    if view.axis != "video":
        return int(record.get("through") or 0) != view.through
    stored = int(record.get("through_ms") or 0)
    if view.complete and not record.get("complete"):
        return view.through > stored or view.cover_end_ms > int(record.get("cover_end_ms") or 0)
    return view.through - stored > REGEN_ADVANCE_MS


def generate_and_store(resource_id: str, *, store=None, force: bool = False) -> dict | None:
    """Build and persist chapters for ``resource_id`` if missing or stale."""
    view = read_resource(resource_id, store=store)
    if view is None:
        return None
    record = load_record(resource_id)
    if not force and not is_stale(record, view):
        return record
    chapters, method = generate(view)
    record = {
        "resource_id": resource_id,
        "source_type": view.source_type,
        "axis": view.axis,
        "method": method,
        "through": view.through,
        "through_ms": view.through if view.axis == "video" else None,
        "cover_end_ms": view.cover_end_ms if view.axis == "video" else None,
        "complete": view.complete,
        "generated_at": int(time.time()),
        "chapters": chapters,
    }
    # The resource may have been deleted while the LLM was thinking.
    if read_resource(resource_id, store=store) is None:
        return None
    save_record(record)
    log.info("chapters for %s: %d (%s)", resource_id, len(chapters), method)
    return record


def schedule_generation(resource_id: str, *, store=None) -> bool:
    """Start background generation unless one is already running for the id."""
    with _LOCK:
        if resource_id in _INFLIGHT:
            return False
        _INFLIGHT.add(resource_id)

    def run() -> None:
        try:
            generate_and_store(resource_id, store=store)
        except Exception:  # a chapter failure is never fatal
            log.exception("chapter generation failed for %s", resource_id)
        finally:
            with _LOCK:
                _INFLIGHT.discard(resource_id)

    try:
        SPAWN(run)
    except Exception:
        with _LOCK:
            _INFLIGHT.discard(resource_id)
        log.exception("could not start chapter generation for %s", resource_id)
        return False
    return True


def is_inflight(resource_id: str) -> bool:
    with _LOCK:
        return resource_id in _INFLIGHT


def reset_inflight() -> None:
    with _LOCK:
        _INFLIGHT.clear()


def _auto_enabled() -> bool:
    if AUTO_GENERATE is not None:
        return AUTO_GENERATE
    return "PYTEST_CURRENT_TEST" not in os.environ


def on_ingest_done(resource_id: str) -> None:
    """Hook for a successful ingest job. Never raises, never blocks."""
    try:
        if resource_id and _auto_enabled():
            schedule_generation(resource_id)
    except Exception:  # ingestion must not fail on chapters
        log.warning("chapter scheduling failed for %s", resource_id, exc_info=True)


def chapters_payload(resource_id: str, *, store=None) -> dict:
    """The ``GET /resources/{id}/chapters`` body."""
    view = read_resource(resource_id, store=store)
    if view is None:
        return {"resource_id": resource_id, "status": STATUS_UNAVAILABLE, "chapters": []}
    record = load_record(resource_id)
    if not is_stale(record, view):
        return {"resource_id": resource_id, "status": STATUS_READY,
                "chapters": list(record["chapters"])}
    schedule_generation(resource_id, store=store)
    # A stale (partial-video) record is still shown while the rebuild runs.
    previous = list(record["chapters"]) if record is not None else []
    return {"resource_id": resource_id, "status": STATUS_PENDING, "chapters": previous}
