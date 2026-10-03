"""Chat orchestration: scope -> rewrite -> retrieve -> prompt -> LLM -> validate."""

from __future__ import annotations

import logging
from collections.abc import Sequence

from app.chat.llm import DeterministicStubLLM
from app.chat.prompts import (
    REFUSAL_TEXT,
    build_grounded_prompt,
    extract_cited_ids,
    rewrite_query,
)
from app.chat.schemas import ChatResponse, Citation
from app.chat.sessions import InMemoryMessageStore, InMemorySessionStore

log = logging.getLogger(__name__)

STRICT_T_MODES = frozenset({"summarize", "quiz"})
VIDEO_SOURCE_TYPES = frozenset({"video", "video_transcript"})


class SessionNotFoundError(LookupError):
    pass


def _passage_to_citation(passage) -> Citation:
    return Citation(
        chunk_id=passage.chunk_id,
        source_type=passage.source_type,
        video_id=passage.video_id,
        start_ms=passage.start_ms,
        end_ms=passage.end_ms,
        page_no=passage.page_no,
        slide_no=passage.slide_no,
    )


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
        from app.retrieval.service import RetrievalService

        return RetrievalService(store=self._store, embedder=self._embedder)

    async def chat(
        self,
        *,
        session_id: str,
        current_time_ms: int,
        message: str,
        mode: str = "qa",
    ) -> ChatResponse:
        from app.chat.launch import clamp_current_time_ms

        session = self._sessions.get(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)

        t_ms = clamp_current_time_ms(current_time_ms)
        allowed = list(session.resource_scope)
        history = self._messages.user_texts(session_id, limit=5)
        rewritten = rewrite_query(message, history)

        retrieval = self._retrieval()
        passages = await retrieval.retrieve(
            rewritten, session.course_id, allowed, t_ms, k=8
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
        citations = [_passage_to_citation(by_id[c]) for c in cited_ids if c in by_id]
        self._persist(
            session_id, message, result.text, [c.model_dump() for c in citations]
        )
        return ChatResponse(answer=result.text, citations=citations, refused=False)

    def _persist(
        self, session_id: str, user_text: str, answer: str, citations: list[dict]
    ) -> None:
        self._messages.append(session_id, "user", user_text, [])
        self._messages.append(session_id, "assistant", answer, citations)
