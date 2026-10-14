"""Describe the frame the learner is actually looking at, right now.

Why this module exists
----------------------
The pre-indexed visual pass samples every ~4s (``visual.FRAME_INTERVAL_S``), so
"what is on screen?" was answered from the *nearest sampled frame* -- which is
up to 4s stale, and, worse, is whichever frame the sampler happened to index.
The learner is looking at a specific instant and the product should answer about
that instant.

The design, stated as one sentence: **the live frame is a passage at exactly T.**
It is modelled as an *instant observation* (``start_ms == end_ms == T``), the
same way ``visual.frame_bounds`` models a sampled frame. That single modelling
choice is the whole reason this needs no special-casing anywhere downstream:

    end_ms <= T   <=>   T <= T        (true)

so the live passage satisfies the strict-T gate trivially and cannot be a
spoiler. Nothing in ``build_where``, ``NO_END_MS``, ``TIME_GATED_SOURCE_TYPES``,
``_passes_strict_t``, ``assert_no_leakage`` or ``apply_strict_timestamp`` is
touched, and ``image`` stays out of ``TIME_GATED_SOURCE_TYPES``.

What this module is NOT allowed to do
-------------------------------------
* Invent a description. If the vision model says nothing usable, that is an
  error (:class:`LiveFrameUnavailable`), not a fallback string.
* Invent a timestamp. ``T`` comes from the client but is *validated* against a
  server-side duration read from the container header
  (:func:`app.ingestion.duration.media_duration_ms`). An out-of-range ``T`` is
  rejected outright -- never clamped onto the first/last frame, which is exactly
  the substitution ``visual._validated_descriptions`` refuses to make for
  sampled frames.
* Fabricate a frame for a document. A PDF/PPTX/image has no timeline and no
  frame; :func:`resource_has_frame` says so and the caller degrades honestly.

Reuse, not duplication
----------------------
The vision call is :func:`app.ingestion.visual._gemini_describe` -- the same
client, the same prompt, the same JSON parser -- reached through its own bounded
retry wrapper :func:`~app.ingestion.visual._describe_with_retry`, and paired
with the same :func:`~app.ingestion.visual._validated_descriptions` trust
boundary that the indexing pass uses. There is no second vision client in this
codebase, and the model never sees a timestamp (the index ``0`` is a label, and
the timestamp is derived on our side from the validated ``T`` -- exactly as it
is for sampled frames).

Caching
-------
Re-asking at the same second must not cost another vision call, so the cache key
is ``(resource_id, floor(T / 1000))`` served by the existing
:mod:`app.cache` disk cache. Within one wall-clock second the rendered frame is
the same image a learner is looking at, so this reuses a billable call without
weakening any timestamp guarantee -- the *bound* stamped on the passage is the
live ``T``, never the cached one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

#: Disk cache name. Reuses :mod:`app.cache` (same atomic-save JSON layer as the
#: transcript/embedding/chat caches) rather than introducing a second cache
#: implementation.
CACHE_NAME = "live_frame"

#: A live frame is a ``visual`` observation: same modality vocabulary as a
#: sampled frame, so ``normalize_modality`` maps it without a special case.
LIVE_FRAME_SOURCE_TYPE = "visual"
LIVE_FRAME_KIND = "visual"

#: Resource kinds that genuinely have a timeline and therefore a frame. Only
#: ``video`` does: a YouTube resource is still indexed as ``video``
#: (``app.ingestion.jobs``).
LIVE_FRAME_CAPABLE_TYPES = frozenset({"video"})

#: Document kinds. Listed explicitly, and the *absence* of a frame is never
#: inferred from "unknown type" -- see :func:`resource_has_frame`, which is
#: fail-closed.
LIVE_FRAME_UNSUPPORTED_TYPES = frozenset({"pdf", "pptx", "image", "note", "code"})

#: JPEG quality requested from ``canvas.toBlob``. Frames are read for text and
#: diagrams, so this matches ``visual.FRAME_JPEG_QUALITY``'s intent; the encoded
#: size is irrelevant next to a vision call.
LIVE_FRAME_JPEG_QUALITY = 0.8


class LiveFrameError(RuntimeError):
    """Base class for every honest failure of the live-frame path."""


class LiveFrameRejected(LiveFrameError):
    """``T`` is outside ``[0, duration_ms]``. Rejected, never clamped.

    A separate class from :class:`LiveFrameUnavailable` because the two deserve
    different client-facing copy: a rejected timestamp is a client bug (or an
    attack), an unavailable description is a provider failure.
    """


class LiveFrameUnavailable(LiveFrameError):
    """No description could be produced for the captured frame."""


@dataclass(frozen=True)
class LiveFrame:
    """A captured frame, described, placed at exactly ``at_ms``.

    ``at_ms`` is the validated ``T``. It is the only timestamp in play: the
    description is filed against the instant it was observed at, never against a
    sample index, a nearest frame, or a model-asserted time.
    """

    resource_id: str
    course_id: str
    at_ms: int
    text: str
    video_id: str | None = None


def live_chunk_id(resource_id: str, at_ms: int) -> str:
    """Stable, citation-safe id for the live frame at ``at_ms``.

    Must match ``app.chat.prompts._ID`` (``[A-Za-z0-9_\\-]+``) or the model could
    not cite it. Stable per ``(resource_id, at_ms)`` so a re-ask at the same
    millisecond produces the same id, which keeps message history coherent.
    """
    return f"live_{resource_id}_{int(at_ms)}"


def live_frame_passage(frame: LiveFrame):
    """Shape a :class:`LiveFrame` as a ``RetrievedPassage``.

    The timestamps are the design: ``start_ms == end_ms == T`` makes the passage
    an instant that satisfies ``end_ms <= T`` with equality, so it passes every
    existing gate without a special case and can never describe material from
    after the playhead.

    Imported lazily: ``app.retrieval.store`` imports ``app.retrieval.recency`` at
    module scope, and a top-level ``store`` import here would be a cycle for any
    module that imports this one during store construction.
    """
    from app.retrieval.store import RetrievedPassage

    at_ms = int(frame.at_ms)
    return RetrievedPassage(
        chunk_id=live_chunk_id(frame.resource_id, at_ms),
        resource_id=frame.resource_id,
        course_id=frame.course_id,
        source_type=LIVE_FRAME_SOURCE_TYPE,
        text=frame.text,
        kind=LIVE_FRAME_KIND,
        start_ms=at_ms,
        end_ms=at_ms,
        video_id=frame.video_id,
        page_no=None,
        slide_no=None,
        image_no=None,
        # A captured frame is a perfect description of T by construction, so it
        # outranks every retrieved passage in the grounded prompt. It does not
        # bypass the gate: it was never in the store to begin with.
        score=1.0,
        # `live` is the ONLY marker of provenance for this passage -- it is not
        # in Chroma and must never be, or a re-ingest would file it as a real
        # frame. `app.chat.service._passage_to_citation` reads it.
        metadata={"kind": LIVE_FRAME_KIND, "live": True},
    )


def assert_live_frame_at(passage, current_time_ms: int) -> None:
    """Re-assert the gate on a live passage. Loud, never repairs.

    The arithmetic makes this always true; the assertion exists because "the
    live passage is at exactly T" is the property the entire feature rests on,
    and it is the kind of thing a later refactor turns into "the nearest frame".
    """
    assert passage.end_ms is not None, "live frame must carry a non-null end_ms"
    assert int(passage.end_ms) == int(passage.start_ms), (
        "a live frame is an instant observation: start_ms must equal end_ms"
    )
    assert int(passage.end_ms) <= int(current_time_ms), (
        f"live frame at {passage.end_ms} is after the playhead {current_time_ms}"
    )


def resource_has_frame(source_type: str | None) -> bool:
    """True only for a resource kind that genuinely has a timeline.

    Fail-closed: an unrecognised or missing ``source_type`` returns ``False``.
    Guessing "probably a video" for an unknown kind is how a document ends up
    with an invented frame.
    """
    return (source_type or "").strip().lower() in LIVE_FRAME_CAPABLE_TYPES


def validate_frame_time_ms(*, at_ms: int, duration_ms: int) -> int:
    """Return ``at_ms`` if it lies in ``[0, duration_ms]``, else raise.

    The server-side duration is the authority; a client-supplied duration is
    never consulted (there is no such parameter, deliberately). An out-of-range
    timestamp is **rejected**, never clamped: clamping ``T`` onto ``0`` or onto
    ``duration_ms`` would silently answer a different question from a different
    frame while the client still believes it asked about its own playhead.
    """
    try:
        t_ms = int(at_ms)
    except (TypeError, ValueError) as exc:
        raise LiveFrameRejected(f"current_time_ms is not an integer: {at_ms!r}") from exc
    if duration_ms is None or int(duration_ms) < 0:
        raise LiveFrameUnavailable(
            "no authoritative duration for this resource, so the frame timestamp "
            "cannot be validated"
        )
    limit = int(duration_ms)
    if t_ms < 0:
        raise LiveFrameRejected(
            f"current_time_ms={t_ms} is before the start of the media (0ms)"
        )
    if t_ms > limit:
        raise LiveFrameRejected(
            f"current_time_ms={t_ms} is past the end of the media ({limit}ms)"
        )
    return t_ms


def resolve_resource_duration_ms(resource_id: str) -> int | None:
    """Server-side duration of a resource's own asset, in milliseconds.

    Read from the container header via
    :func:`app.ingestion.duration.media_duration_ms` -- the same authoritative
    source ingestion uses, and the same module that refuses to guess when the
    header lies. ``None`` means "unknown", which callers treat as a hard stop.

    Imports are deferred: ``app.ingestion.router`` imports the ingestion pipeline
    at module scope, and this module is imported from ``app.chat``.
    """
    from app.ingestion.duration import media_duration_ms
    from app.ingestion.router import resolve_asset

    asset = resolve_asset(resource_id)
    if asset is None:
        return None
    return media_duration_ms(asset)


def frame_cache_key(resource_id: str, at_ms: int) -> str:
    """``(resource_id, floor(T/1000))`` -- one vision call per resource-second.

    The second-bucket key is deliberate: re-asking while still paused on the
    same frame is the common case, and the rendered frame within one second is
    the same image. It never widens a timestamp -- the passage is stamped with
    the live ``T``, only the *description* is reused.
    """
    from app.cache import sha1hex

    return "liveframe:" + sha1hex(resource_id, str(int(at_ms) // 1000))


def _cache(cache_dir=None):
    from app.cache import DiskCache, get_cache

    if cache_dir is not None:
        # Test seam: a tmp_path, so a test run can never write into the
        # developer's apps/api/data/caches/.
        return DiskCache(CACHE_NAME, cache_dir=cache_dir)
    return get_cache(CACHE_NAME)


def describe_live_frame(
    jpeg_bytes: bytes,
    *,
    resource_id: str,
    at_ms: int,
    cache_dir=None,
) -> str:
    """Describe one captured JPEG frame. Returns the model's description text.

    Reuses the indexing pass's vision call verbatim
    (:func:`app.ingestion.visual._gemini_describe`) and its trust boundary
    (:func:`~app.ingestion.visual._validated_descriptions`): the model is shown
    a single frame labelled ``index 0`` and never a timestamp, and a reply that
    names any other index is discarded rather than filed anywhere.

    Raises :class:`LiveFrameUnavailable` when the model produces nothing
    legible -- the caller must refuse or degrade, never substitute a placeholder
    description, because a fabricated frame description is indistinguishable
    from a real one in the answer.
    """
    from app.ingestion.visual import (
        _EMPTY_DESCRIPTIONS,
        _describe_with_retry,
        _validated_descriptions,
    )

    key = frame_cache_key(resource_id, at_ms)
    cache = _cache(cache_dir)
    from app.cache import cache_enabled

    if cache_enabled():
        cached = cache.get(key)
        if isinstance(cached, str) and cached.strip():
            log.info("live frame: cache hit %s (bucket %ss)", resource_id, int(at_ms) // 1000)
            return cached

    # The indexing pass's own describe wrapper: same `_gemini_describe` call,
    # same prompt, same client, plus the bounded retry on a rate limit or an
    # empty/truncated reply. It never raises -- it reports the failure -- which
    # is exactly the contract this path needs: a retry that can rescue a
    # throttled call, and an honest error when even that fails.
    #
    # The retry is not cosmetic. A live probe hit `no usable description` on the
    # very first call after a fresh start (free-tier throttle on the describe
    # endpoint), answered 422, and produced nothing. Retrying turned the same
    # request into a correct answer -- and would NOT have produced a fabricated
    # description if every attempt had failed.
    result, error = _describe_with_retry([(0, jpeg_bytes)])
    if error:
        raise LiveFrameUnavailable(error)
    # Same trust boundary as the indexing pass: only an index the sampler
    # actually produced may survive, and here the only real index is 0.
    kept, notes = _validated_descriptions(dict(result), {0})
    for note in notes:
        log.warning("live frame: %s", note)
    text = str(kept.get(0) or "").strip()
    if not text or text.lower() in _EMPTY_DESCRIPTIONS:
        raise LiveFrameUnavailable(
            "the model returned no usable description for the captured frame"
        )
    if cache_enabled():
        cache.set(key, text)
    return text
