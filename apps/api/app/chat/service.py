"""Chat orchestration: scope -> rewrite -> retrieve -> prompt -> LLM -> validate."""

from __future__ import annotations

import logging
from collections.abc import Sequence

from app.chat.llm import DeterministicStubLLM
from app.chat.prompts import (
    REFUSAL_TEXT,
    SCOPE_REFUSAL_TEXT,
    build_grounded_prompt,
    extract_cited_ids,
    rewrite_query,
    strip_citation_markers,
)
from app.chat.schemas import ChatResponse, Citation, normalize_modality
from app.chat.sessions import InMemoryMessageStore, InMemorySessionStore
from app.retrieval.recency import is_recency_seeking

log = logging.getLogger(__name__)

STRICT_T_MODES = frozenset({"summarize", "quiz"})
VIDEO_SOURCE_TYPES = frozenset({"video", "video_transcript"})


class SessionNotFoundError(LookupError):
    pass


def _passage_to_citation(passage) -> Citation:
    return Citation(
        chunk_id=passage.chunk_id,
        source_type=passage.source_type,
        # Spoken audio vs sampled frame. A chunk with no recorded `kind`
        # yields None -- never a guessed "transcript".
        modality=normalize_modality(getattr(passage, "kind", None)),
        video_id=passage.video_id,
        start_ms=passage.start_ms,
        end_ms=passage.end_ms,
        page_no=passage.page_no,
        slide_no=passage.slide_no,
    )


def effective_resource_scope(
    granted_scope: Sequence[str], focus_resource_id: str | None
) -> list[str]:
    """The resources this request may actually read: grant INTERSECT focus.

    Focus is a *narrowing preference*, never an authority. It is applied as an
    intersection here (and again, structurally, inside the store's ``where``
    clause) precisely because an earlier revision REPLACED the grant with the
    focused id -- which let a caller focus an ungranted resource and read it.

    An empty result means the session's materials do not cover what the learner
    is looking at right now (stale grant, deleted resource, or simply a
    different resource). It is never widened or substituted.
    """
    granted = list(granted_scope or [])
    if focus_resource_id:
        granted = [r for r in granted if r == focus_resource_id]
    return granted


def apply_strict_timestamp(passages: Sequence, current_time_ms: int, mode: str):
    """Defense-in-depth T gate for summarize/quiz (exact ``end_ms`` semantics).

    Keeps a video/``video_transcript`` passage only when ``end_ms`` is not
    None and ``end_ms <= T``; keeps every other source type unconditionally
    (T never applies to docs). Mirrors the retrieval store gate and
    ``assert_no_leakage``. Drops are logged at warning level.
    """
    if mode not in STRICT_T_MODES:
        return list(passages)
    t_ms = int(current_time_ms)
    kept: list = []
    dropped = 0
    for p in passages:
        source_type = getattr(p, "source_type", None)
        if source_type in VIDEO_SOURCE_TYPES:
            end_ms = getattr(p, "end_ms", None)
            if end_ms is not None and int(end_ms) <= t_ms:
                kept.append(p)
            else:
                dropped += 1
        else:
            kept.append(p)
    if dropped:
        log.warning("strict-T filter dropped %d passage(s) for mode=%s", dropped, mode)
    return kept


class ChatService:
    def __init__(
        self,
        store=None,
        embedder=None,
        llm=None,
        sessions: InMemorySessionStore | None = None,
        messages: InMemoryMessageStore | None = None,
    ) -> None:
        from app.chat.sessions import message_store, session_store

        if store is None:
            from app.retrieval.registry import get_shared_store

            store = get_shared_store()
        self._store = store
        self._embedder = embedder
        self._llm = llm or DeterministicStubLLM()
        self._sessions = sessions or session_store
        self._messages = messages or message_store

    def _retrieval(self):
        """Bind the awaitable retrieval seam.

        Three independent strict-T layers now sit between the store and the
        answer, and all three must stay:
          1. the store's Chroma ``where`` filter (structural)
          2. ``store.assert_no_leakage()`` inside ``RetrievalService``
          3. ``apply_strict_timestamp()`` below, post-retrieval

        Each layer has failed independently in this project's history
        (ADR-002 section 5.1), so the redundancy is the point rather than
        an oversight.
        """
        from app.retrieval.service import RetrievalService

        return RetrievalService(store=self._store, embedder=self._embedder)

    async def chat(
        self,
        *,
        session_id: str,
        current_time_ms: int,
        message: str,
        mode: str = "qa",
        focus_resource_id: str | None = None,
    ) -> ChatResponse:
        from app.chat.launch import clamp_current_time_ms

        session = self._sessions.get(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)

        t_ms = clamp_current_time_ms(current_time_ms)
        allowed = effective_resource_scope(session.resource_scope, focus_resource_id)
        if not allowed:
            # Fail closed and say WHY. Without this branch the request reached
            # retrieval with an impossible scope and came back through the
            # ordinary low-evidence path, telling the learner they had not
            # watched far enough -- a content explanation for what is actually a
            # stale session grant. `scope_empty` makes that distinguishable.
            log.warning(
                "empty_scope: session grant does not cover the focused resource"
            )
            self._persist(session_id, message, SCOPE_REFUSAL_TEXT, [])
            return ChatResponse(
                answer=SCOPE_REFUSAL_TEXT, citations=[], refused=True, scope_empty=True
            )

        history = self._messages.user_texts(session_id, limit=5)
        rewritten = rewrite_query(message, history)

        # Present-tense questions ("what is on screen right now?") ask about the
        # playhead, not about the course. Retrieval answers them from material
        # near T instead of the best-matching chunk anywhere in the gated
        # window. This is a preference INSIDE the already-gated pool, applied by
        # the store after the strict-T filter -- the gate itself is untouched.
        prefer_recent = is_recency_seeking(message)

        retrieval = self._retrieval()
        passages = await retrieval.retrieve(
            rewritten,
            session.course_id,
            allowed,
            t_ms,
            k=8,
            focus_resource_id=focus_resource_id,
            prefer_recent=prefer_recent,
        )
        passages = apply_strict_timestamp(passages, t_ms, mode)

        if not passages:
            self._persist(session_id, message, REFUSAL_TEXT, [])
            return ChatResponse(answer=REFUSAL_TEXT, citations=[], refused=True)

        system, user_prompt = build_grounded_prompt(
            rewritten, passages, mode=mode, history=history
        )
        result = await self._llm.complete(
            system=system, user_prompt=user_prompt, passages=passages, mode=mode
        )

        # A refusal answer carries no citations by design.
        if result.text == REFUSAL_TEXT:
            self._persist(session_id, message, REFUSAL_TEXT, [])
            return ChatResponse(answer=REFUSAL_TEXT, citations=[], refused=True)

        cited_ids = list(result.cited_ids) or extract_cited_ids(result.text)
        # Parse answer text too so hand-written providers cannot smuggle IDs
        # past the provider's own cited_ids list.
        for cid in extract_cited_ids(result.text):
            if cid not in cited_ids:
                cited_ids.append(cid)

        allowed_ids = {p.chunk_id for p in passages}
        unknown = [c for c in cited_ids if c not in allowed_ids]
        if unknown:
            log.warning("unknown citation(s) rejected: %s", unknown)
            self._persist(session_id, message, REFUSAL_TEXT, [])
            return ChatResponse(answer=REFUSAL_TEXT, citations=[], refused=True)

        if not cited_ids:
            # H5: a substantive answer that cites nothing is ungrounded.
            # One repair retry before refusing: the model often complies when
            # the failure is named explicitly.
            log.warning("uncited_answer: attempting one cite-repair retry")
            from app.chat.prompts import CITE_REPAIR_INSTRUCTION

            repair_prompt = (
                f"{user_prompt}\n\nPrevious uncited answer:\n{result.text}\n\n"
                f"{CITE_REPAIR_INSTRUCTION}"
            )
            result = await self._llm.complete(
                system=system,
                user_prompt=repair_prompt,
                passages=passages,
                mode=mode,
            )
            if result.text == REFUSAL_TEXT:
                self._persist(session_id, message, REFUSAL_TEXT, [])
                return ChatResponse(answer=REFUSAL_TEXT, citations=[], refused=True)
            cited_ids = list(result.cited_ids) or extract_cited_ids(result.text)
            for cid in extract_cited_ids(result.text):
                if cid not in cited_ids:
                    cited_ids.append(cid)
            unknown = [c for c in cited_ids if c not in allowed_ids]
            if unknown:
                log.warning("unknown citation(s) rejected after repair: %s", unknown)
                self._persist(session_id, message, REFUSAL_TEXT, [])
                return ChatResponse(answer=REFUSAL_TEXT, citations=[], refused=True)

        if not cited_ids:
            log.warning(
                "uncited_answer: non-refusal answer with empty cited_ids refused"
            )
            self._persist(session_id, message, REFUSAL_TEXT, [])
            return ChatResponse(answer=REFUSAL_TEXT, citations=[], refused=True)

        by_id = {p.chunk_id: p for p in passages}
        # Validation above already ran against every distinct cited id.
        # Every validated citation is surfaced individually, in cited order:
        # overlapping spans from sliding transcript windows are all shown so the
        # learner can see every supporting chunk.
        cited_passages = [by_id[c] for c in cited_ids if c in by_id]
        citations = [_passage_to_citation(p) for p in cited_passages]
        # Validation above ran against the raw text, so it is now safe to drop
        # the transport markers: the UI renders badges from `citations[]`.
        answer = strip_citation_markers(result.text)
        self._persist(session_id, message, answer, [c.model_dump() for c in citations])
        return ChatResponse(answer=answer, citations=citations, refused=False)

    def _persist(
        self, session_id: str, user_text: str, answer: str, citations: list[dict]
    ) -> None:
        self._messages.append(session_id, "user", user_text, [])
        self._messages.append(session_id, "assistant", answer, citations)
