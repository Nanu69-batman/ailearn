"""Recency preference for "what is in front of me now?" questions.

The product promise is "answers grounded only in what the learner has watched
so far". Pure top-k similarity over everything ``<= T`` serves that promise
for *conceptual* questions ("what is a partial derivative") but not for
*present-tense* ones ("what is on screen right now?"): those are not asking
"what in the course is similar to this text", they are asking "what is
happening at the playhead". Answering them from the top of the video is wrong
even though every citation is legal under the strict-T gate.

This module is deliberately tiny and mechanical:

* :func:`is_recency_seeking` -- phrase/regex match, no LLM call. A classifier
  that costs a network round-trip and can disagree with itself is not worth
  it for a fixed, closed-class set of markers.
* :func:`is_frame_question` -- a SIBLING classifier for the narrower question
  "is this about the frame on the screen *right now*?". It only decides whether
  to spend a vision call on a live capture; it never gates, filters or re-ranks
  anything, and it leaves :func:`is_recency_seeking` behaviour untouched.
* :func:`apply_recency_preference` -- reorders passages that are ALREADY
  proven eligible by the store's ``where`` filter and ``_passes_strict_t``.

The gate is untouched by design. This module runs strictly downstream of
:meth:`app.retrieval.store.ChromaVectorStore._passes_strict_t` and only ever
makes the candidate set *smaller or differently ordered*:

* a passage with ``end_ms > T`` can never reach this function;
* ``end_ms is None`` is dropped, never "treated as current";
* the only passages removed here are passages that are *too stale to be an
  answer to a present-tense question* -- removing them is a narrowing, so the
  gate cannot be widened by this file.

If nothing survives, retrieval returns nothing and the learner gets the
ordinary low-evidence refusal. That is the correct outcome: an honest
"nothing is happening right now that I can see" beats a confident answer
about the first ten minutes of the video.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids a runtime import cycle
    from app.retrieval.store import RetrievedPassage


def _gated_types() -> frozenset[str]:
    """``TIME_GATED_SOURCE_TYPES``, imported lazily.

    ``store`` imports this module at module scope, so a top-level import here
    would be a cycle. Deferred to call time; the set is a module constant, so
    this costs a dict lookup.
    """
    from app.retrieval.store import TIME_GATED_SOURCE_TYPES

    return TIME_GATED_SOURCE_TYPES

#: How far behind the playhead a passage may be and still count as "right now".
#: Deliberately a *band*, not a pinpoint: the question is "is this the current
#: part of the lesson", and course media is chunked every few seconds, so the
#: band only has to exclude material from a different part of the video. Sized
#: above a typical lesson segment so a real question is not starved by a
#: marginally-too-tight window; anything outside it is dropped and the learner
#: gets an honest refusal rather than an answer about a different scene.
RECENCY_FLOOR_MS = 600_000

#: How much proximity to the playhead is worth in score units (0-1). Applied
#: on top of the cosine score, so a strictly closer passage outranks a
#: marginally more similar distant one, but similarity still breaks ties
#: inside the band.
RECENCY_WEIGHT = 0.6

#: Present-tense markers. Kept as literal phrases rather than a POS pass --
#: the set is closed and small, and each entry is a phrase a learner would
#: actually type. Substring matching on word boundaries avoids "screenplays"
#: matching "screen".
RECENCY_MARKERS: tuple[str, ...] = (
    r"\bright now\b",
    r"\bat (?:this|the|that) moment\b",
    r"\bjust now\b",
    r"\bon[- ]screen\b",
    r"\bcurrently\b",
    (
        r"\bwhat(?:'s| is| are| does| do)?\s+"
        r"(?:it|this|that|the (?:video|slide|screen|board|diagram|frame|graph))"
        r"\s+(?:show|display|depict|present)\w*"
    ),
    r"\bwhat does (?:it|this|the (?:video|slide|screen|board))\s+show\b",
    r"\bbeing (?:shown|displayed|presented)\b",
    r"\bin front of me\b",
    r"\bthis (?:moment|second|frame|slide)\b",
    r"\bmost recent(?:ly)?\b",
    r"\bjust played\b",
    r"\bhappening (?:now|at the moment)\b",
)

#: Questions about a *time-invariant attribute* of something visible -- colour,
#: size, shape, a label written on it. Deliberately NOT recency markers.
#:
#: These name a surface ("the curve in the diagram") but ask about a property
#: that does not change as the playhead moves, so the honest answer is the one
#: that best describes the attribute, anywhere in the gated window. Treating
#: them as recency-seeking narrows the candidate set to a band near T and, when
#: no frame near T happens to mention the curve, converts a previously correct,
#: gate-valid answer into a refusal. That was a real regression:
#: "What colour is the curve in the diagram?" at T=600000 answered "blue" from a
#: visual chunk ending at 116000 (``end_ms <= T``), and refused after the
#: attribute question was folded into the recency classifier.
#:
#: Recency means "what is happening at the playhead right now", not "anything
#: visual". This list exists so that boundary is stated rather than implied;
#: there is no exclusion regex for it, because none of the surviving markers
#: match an attribute question in the first place.
NON_RECENCY_ATTRIBUTE_PHRASES: tuple[str, ...] = (
    "what colour",
    "what color",
    "how big",
    "what size",
    "what shape",
    "what label",
    "what does it say",
)

_RECENCY_RE = re.compile("|".join(RECENCY_MARKERS), re.IGNORECASE)

#: Explicit references to a DIFFERENT (usually earlier) moment. A question
#: like "what equation is on screen in the first minute?" mentions "on screen"
#: but is asking about a specific earlier point, so the recency band would
#: answer it wrongly. One narrow exclusion list; anything not matched here is
#: still treated as present-tense.
PAST_SCOPE_MARKERS: tuple[str, ...] = (
    r"\b(?:in|at|during) the (?:first|second|third|last|final|opening|beginning|start|end)\b",
    r"\b(?:earlier|previously|before|at the start|at the beginning|already)\b",
    r"\b\d+(?:st|nd|rd|th)?\s+(?:minute|second|mark|chapter|part|lesson)\b",
)

_PAST_SCOPE_RE = re.compile("|".join(PAST_SCOPE_MARKERS), re.IGNORECASE)


#: Questions that ask about *the frame currently on the learner's screen* --
#: the class that must be answered from a live capture rather than from the
#: nearest pre-indexed frame description.
#:
#: This is a SIBLING of :func:`is_recency_seeking`, not a replacement, and it is
#: deliberately independent of it: recency re-ranks already-gated passages
#: toward the playhead, whereas this decides whether to spend a vision call on
#: the learner's actual screen. A question can be one, both, or neither
#: ("what is on screen right now?" is both; "what colour is the curve" is
#: neither; "what am I looking at" is only this one).
#:
#: Same style as the markers above -- literal phrases a learner would actually
#: type, word-bounded, no LLM call. A classifier that costs a network round-trip
#: and can disagree with itself is not worth it for a closed class of markers.
#:
#: Deliberately NOT matched here (they name a *property*, not the current
#: instant, so capturing a frame would answer the wrong question):
#: ``what colour is the curve in the diagram``, ``what does it say`` as a
#: pronoun phrase with no on-screen referent, ``what is on the board`` where
#: "board" is the physical whiteboard rather than the frame. The attribute
#: phrases in :data:`NON_RECENCY_ATTRIBUTE_PHRASES` are handled the same way
#: here: by simply not appearing below.
FRAME_QUESTION_MARKERS: tuple[str, ...] = (
    r"\bwhat am i looking at\b",
    r"\bwhat'?s (?:on|at|in) (?:my |this |the )?(?:screen|frame|slide|page)\b",
    (
        r"\b(?:describe|explain) (?:this|the|current) "
        r"(?:frame|screen|slide|scene|still|page)\b"
    ),
    r"\bthis (?:frame|screen|slide|still)\b",
    r"\bcurrent(?:ly)? (?:shown|on screen|displayed) (?:frame|slide)\b",
    (
        r"\b(?:what|which) (?:does|do|is|are)? ?"
        r"(?:it|this|the (?:screen|frame|slide|board)) "
        r"(?:say|says|read|reads|show|shows|contain|contains|display|displays)\b"
    ),
    r"\bread (?:the|this) (?:screen|frame|slide|board|display)\b",
    (
        r"\b(?:what|which) (?:text|equation|formula|word|words|title|label|heading)"
        r"(?:s)? (?:is|are)? ?(?:on|in) (?:the|this) "
        r"(?:screen|frame|slide|board)\b"
    ),
    # Bare "on screen" / "on-screen", matching RECENCY_MARKERS' own spelling so
    # the two classifiers agree on the same surface. "what equation is on
    # screen" is the single most common way a learner asks this and it carries no
    # determiner at all.
    r"\bon[- ]screen\b",
    r"\b(?:on|in) (?:the|this) (?:screen|frame|slide)\b",
)

_FRAME_QUESTION_RE = re.compile("|".join(FRAME_QUESTION_MARKERS), re.IGNORECASE)


def is_frame_question(text: str) -> bool:
    """True when the question is about the frame currently on screen.

    Pure phrase matching -- no LLM call, no network, no state. Deliberately
    conservative in both directions, same contract as
    :func:`is_recency_seeking`:

    * A miss costs one capture opportunity; the question is answered by ordinary
      retrieval, which is exactly the previous behaviour.
    * A false positive costs one vision call and a high-priority live-frame
      passage in the prompt. It cannot widen the gate: the live passage is at
      exactly ``T``, so it is always legal evidence and never a spoiler.

    An explicit reference to a DIFFERENT moment ("in the first minute",
    "earlier") overrides a positive marker, exactly as in
    :func:`is_recency_seeking` -- such a question is deliberately NOT about the
    current screen, and capturing a frame for it would answer a different
    question than the one asked.

    The explicit player button forces this on regardless of wording; that
    override is applied by the caller (``app.chat.router``), not here, so this
    function stays a pure function of the text.
    """
    if not text:
        return False
    if _PAST_SCOPE_RE.search(text):
        return False
    return bool(_FRAME_QUESTION_RE.search(text))


def is_recency_seeking(text: str) -> bool:
    """True when the question is about the playhead, not the course.

    Deliberately conservative in both directions:

    * A miss costs nothing -- the question is answered by ordinary top-k, which
      is exactly the previous behaviour.
    * A false positive costs a narrowed candidate set, which can only produce
      an honest refusal, never a spoiler.

    Conceptual questions ("what is a partial derivative") contain none of the
    markers and stay on the plain semantic path. An explicit reference to a
    different moment ("in the first minute", "earlier") overrides a positive
    marker, because such a question is deliberately NOT about the playhead.

    Attribute questions ("what colour is the curve in the diagram") are not
    recency markers and no marker below matches them, so they fall through to
    the plain top-k path and are answered from anywhere in the gated window.
    A present-tense marker still wins when one is genuinely present -- "what
    colour is on screen right now" is recency-seeking, because the learner named
    the playhead, not a static attribute.
    """
    if not text:
        return False
    if _PAST_SCOPE_RE.search(text):
        return False
    return bool(_RECENCY_RE.search(text))


def recency_key(passage: RetrievedPassage, current_time_ms: int) -> float:
    """Ranking score: cosine similarity nudged by proximity to the playhead.

    Docs (png/pdf/pptx pages) are not moments in time, so they get no recency
    bonus at all -- ``end_ms`` is ``None`` by construction for them.
    """
    base = float(passage.score)
    if passage.source_type not in _gated_types():
        return base
    end_ms = passage.end_ms
    if end_ms is None:
        return base
    delta_ms = int(current_time_ms) - int(end_ms)
    if delta_ms <= 0:
        # Exactly at (or, defensively, past) the playhead: maximally current.
        return base + RECENCY_WEIGHT
    closeness = max(0.0, 1.0 - (delta_ms / float(RECENCY_FLOOR_MS)))
    return base + (RECENCY_WEIGHT * closeness)


def apply_recency_preference(
    passages: list[RetrievedPassage],
    current_time_ms: int,
    *,
    floor_ms: int = RECENCY_FLOOR_MS,
) -> list[RetrievedPassage]:
    """Drop passages too stale to answer "what is on screen?", then re-rank.

    Only ever called on the store's already-gated ``eligible`` list, so this
    is a *narrowing* of the candidate set. Returns the same objects, sorted by
    :func:`recency_key` descending; Python's sort is stable, so passages with
    equal keys keep their original distance order.
    """
    t_ms = int(current_time_ms)
    current: list[RetrievedPassage] = []
    for p in passages:
        if p.source_type not in _gated_types():
            current.append(p)  # docs: a page is not a moment in time
            continue
        if p.end_ms is None:
            continue  # unknown end: EXCLUDE, never "assume it is current"
        if int(p.end_ms) > t_ms:
            # Defence in depth. The store's ``where`` clause and
            # ``_passes_strict_t`` already make this unreachable in
            # production; re-asserted here because "the most recent passage"
            # is exactly the phrasing a future refactor would turn into a
            # "latest chunk" lookup, and that lookup must never mean "from
            # the future". Excluding, never re-admitting.
            continue
        if t_ms - int(p.end_ms) > floor_ms:
            continue  # too far behind the playhead to be "right now"
        current.append(p)
    current.sort(key=lambda p: recency_key(p, t_ms), reverse=True)
    return current