"""Materials this chat may not read that would answer the question.

A chat in a subject reads only the materials ticked for it. When an answer
comes back refused (or taught from general knowledge because the readable
materials did not cover it), the client may name the subject's OTHER
materials (``ChatRequest.suggest_resource_ids``). This searches just those
and returns the ids of the ones that match well, so the client can ask the
learner whether to give the chat access.

Only resource ids leave this module -- never a passage, a time or a page --
and the candidates are intersected with the session's FULL grant, so it can
only point at materials the learner already has. Videos are searched as if
completed (``COMPLETED_T_MS``): that is exactly what granting access would
let the chat read, and nothing about them is shown until the learner agrees.
Best-effort: any failure means "no suggestion", never a failed answer.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from app.chat.completed import COMPLETED_T_MS

log = logging.getLogger(__name__)

#: Cosine similarity a passage needs before its material is suggested.
MIN_SCORE = 0.6
#: Passages searched across the candidates.
SUGGEST_K = 6
#: At most this many materials are suggested.
MAX_SUGGESTIONS = 3


def suggest_candidates(
    granted: Sequence[str], readable: Sequence[str], requested: Sequence[str]
) -> list[str]:
    """Requested ids the session was granted and this chat cannot read yet."""
    want = set(requested)
    have = set(readable)
    return [r for r in granted if r in want and r not in have]


def pick_suggestions(passages: Sequence, min_score: float = MIN_SCORE) -> list[str]:
    """Distinct resource ids of well-matching passages, best first."""
    out: list[str] = []
    for p in sorted(passages, key=lambda p: float(getattr(p, "score", 0.0)), reverse=True):
        rid = getattr(p, "resource_id", None)
        if not rid or float(getattr(p, "score", 0.0)) < min_score or rid in out:
            continue
        out.append(rid)
        if len(out) >= MAX_SUGGESTIONS:
            break
    return out


async def suggest_materials(
    service, session, message: str, candidates: Sequence[str]
) -> list[str]:
    """Ids among ``candidates`` whose content matches ``message``."""
    if not candidates or session is None:
        return []
    try:
        retrieval = service._retrieval()
        passages = await retrieval.retrieve(
            message, session.course_id, list(candidates), COMPLETED_T_MS, k=SUGGEST_K
        )
    except Exception as exc:  # noqa: BLE001 - a suggestion must never fail the answer
        log.warning("material suggestion skipped: %s", type(exc).__name__)
        return []
    return pick_suggestions(passages)
