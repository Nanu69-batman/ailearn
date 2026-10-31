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
    session was not granted is dropped, never added.

    Without a focused resource (a Malai chat: no player, so T is 0) the grant
    is in play but every video in it is still gated at T=0 and reads nothing.
    The ones marked done are therefore read here, in full, exactly as with a
    focus -- otherwise a video ticked in a Malai chat could never be read.
    """
    if not completed_resource_ids:
        return []
    done = set(completed_resource_ids)
    return [r for r in granted_scope or [] if r in done and r != focus_resource_id]


def merge_completed(primary: Sequence, extra: Sequence) -> list:
    """The open material's passages first, then new ones from completed ones."""
    have = {p.chunk_id for p in primary}
    return [*primary, *(p for p in extra if p.chunk_id not in have)]


def done_limit(resource_id: str, limits: dict[str, int] | None) -> int:
    """The T a done material is read at: its own limit, else the end of time."""
    if limits and resource_id in limits:
        return max(0, min(int(limits[resource_id]), COMPLETED_T_MS))
    return COMPLETED_T_MS


async def retrieve_done(retrieval, query: str, course_id: str, done, limits, *, k: int) -> list:
    """Retrieve from the done materials, each through the strict gate at its T.

    A video granted only "till you've watched" carries a limit (ms): it is
    read through ``end_ms <= limit`` like any playhead. The rest are read at
    COMPLETED_T_MS. One retrieval per distinct T, merged in order.
    """
    groups: dict[int, list[str]] = {}
    for r in done:
        groups.setdefault(done_limit(r, limits), []).append(r)
    out: list = []
    for t, ids in sorted(groups.items(), key=lambda g: -g[0]):
        out = merge_completed(out, await retrieval.retrieve(query, course_id, ids, t, k=k))
    return out
