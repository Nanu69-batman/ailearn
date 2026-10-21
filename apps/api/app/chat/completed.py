"""Course materials the learner marked done, readable beside the open one.

A chat is pinned to the material that is open (grant INTERSECT focus, gated at
the playhead). Materials ticked off in the course checklist were studied in
full, so they are read too -- through the SAME structural strict-T gate, only
evaluated at the end of time instead of the open video's playhead. Unknown
ends are still excluded, and the list is intersected with the session grant,
so it can never reach a material the session was not given.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.chat.schemas import MAX_CURRENT_TIME_MS

#: The T a completed material is read at: the end of the longest allowed video.
COMPLETED_T_MS = MAX_CURRENT_TIME_MS

#: How many passages the completed materials add to the open material's own.
COMPLETED_K = 4


def completed_scope(
    granted_scope: Sequence[str],
    focus_resource_id: str | None,
    completed_resource_ids: Sequence[str],
) -> list[str]:
    """Materials marked done that this session may read, besides the open one.

    grant INTERSECT completed, minus the focused resource (which keeps its own
    playhead gate). Like focus, the list only ever narrows the grant: an id the
    session was not granted is dropped, never added. Without a focused resource
    the whole grant is already in play, so this is empty.
    """
    if not focus_resource_id or not completed_resource_ids:
        return []
    done = set(completed_resource_ids)
    return [r for r in granted_scope or [] if r in done and r != focus_resource_id]


def merge_completed(primary: Sequence, extra: Sequence) -> list:
    """The open material's passages first, then new ones from completed ones."""
    have = {p.chunk_id for p in primary}
    return [*primary, *(p for p in extra if p.chunk_id not in have)]
