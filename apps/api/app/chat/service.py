"""Chat orchestration: scope -> rewrite -> retrieve -> prompt -> LLM -> validate."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence

from app.chat.intent import (
    COVERAGE_QUERY,
    INTENT_MODES,
    SMALLTALK,
    classify_intent,
    has_subject_content,
)
from app.chat.llm import DeterministicStubLLM
from app.chat.prompts import (
    CONVERSATIONAL_FALLBACK_TEXT,
    REFUSAL_TEXT,
    SCOPE_REFUSAL_TEXT,
    build_conversational_prompt,
    build_grounded_prompt,
    build_supplemental_prompt,
    extract_cited_ids,
    rewrite_query,
    strip_citation_markers,
)
from app.chat.schemas import ChatResponse, Citation, normalize_modality
from app.chat.sessions import InMemoryMessageStore, InMemorySessionStore
from app.ingestion.liveframe import assert_live_frame_at, live_frame_passage
from app.retrieval.recency import (
    is_frame_question,
    is_recency_seeking,
    is_supplemental_request,
)
from app.retrieval.store import TIME_GATED_SOURCE_TYPES

log = logging.getLogger(__name__)

PROCESSING_PENDING_TEXT = (
    "This part of the video hasn't been processed yet, so I can't answer "
    "about it reliably. Please try again in a little while, or ask about "
    "an earlier point in the video."
)

STRICT_T_MODES = frozenset({"summarize", "quiz"})
VIDEO_SOURCE_TYPES = frozenset({"video", "video_transcript"})

#: Modes that may never fall back to un-grounded general knowledge.
#:
#: ``summarize`` and ``quiz`` are *about the covered material itself*: there is
#: nothing for "general knowledge" to contribute to a summary of what has been
#: watched, so a fallback there would be answering a different question.
SUPPLEMENTAL_DISABLED_MODES = frozenset({"summarize", "quiz"})


class SessionNotFoundError(LookupError):
    pass


def _passage_to_citation(passage) -> Citation:
    meta = getattr(passage, "metadata", None)
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
        # Images are ungated (absent from TIME_GATED_SOURCE_TYPES) and carry
        # no timeline, so their citation handle is an image number -- exactly
        # the role page_no plays for a pdf. `getattr` because a caller may hand
        # in a passage object predating the field.
        image_no=getattr(passage, "image_no", None),
        text=(getattr(passage, "text", None) or None),
        # A live capture (app.ingestion.liveframe). Provenance lives only in
        # `metadata["live"]`, so a pre-indexed chunk can never set this, and an
        # absent/foreign metadata dict reads as "not live".
        live=bool(isinstance(meta, dict) and meta.get("live")),
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


VISUAL_PENDING_REFUSAL_TEXT = (
    "I could not find this in what has been indexed so far. What is on screen "
    "for this part of the video is still being processed, so try again in a "
    "moment."
)


class ChatService:
    def __init__(
        self,
        store=None,
        embedder=None,
        llm=None,
        sessions: InMemorySessionStore | None = None,
        messages: InMemoryMessageStore | None = None,
        agent=None,
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
        # Optional :class:`app.chat.agent.LessonAgent`. ``None`` (the default,
        # and every test that does not opt in) means the classic pipeline only.
        self._agent = agent

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

    def _pending_frontier(self, allowed: list[str], t_ms: int) -> int | None:
        """Frontier F if a granted time-gated video is partial and ``t_ms > F``.

        Reads only resources already inside the granted (intersected) scope.
        A store without frontier support, a resource with no marker, or one
        whose known duration the marker has reached, yields ``None``.
        """
        lookup = getattr(self._store, "processing_frontier", None)
        if lookup is None:
            return None
        for rid in allowed:
            info = lookup(rid)
            if not info or info.get("source_type") not in TIME_GATED_SOURCE_TYPES:
                continue
            f = info.get("processed_through_ms")
            if f is None:
                continue
            duration = info.get("duration_ms")
            if duration is not None and f >= duration:
                continue
            if t_ms > f:
                return f
        return None

    def _visual_frontier(self, allowed: list[str], t_ms: int) -> int | None:
        """``visual_through_ms`` V if a granted video's visuals lag the playhead.

        Only resources carrying a ``visual_through_ms`` marker are considered
        (legacy resources never are). Called after the transcript-frontier
        check, so ``t_ms <= processed_through_ms`` for any marked video.
        """
        lookup = getattr(self._store, "processing_frontier", None)
        if lookup is None:
            return None
        for rid in allowed:
            info = lookup(rid)
            if not info or info.get("source_type") not in TIME_GATED_SOURCE_TYPES:
                continue
            v = info.get("visual_through_ms")
            if v is None:
                continue
            if t_ms > v:
                return v
        return None

    async def chat(
        self,
        *,
        session_id: str,
        current_time_ms: int,
        message: str,
        mode: str = "qa",
        focus_resource_id: str | None = None,
        live_frame=None,
        no_frame_note: str | None = None,
    ) -> ChatResponse:
        """Answer one learner question; flags ``visual_pending`` when visuals lag."""
        from app.chat.launch import clamp_current_time_ms

        visual_through: int | None = None
        session = self._sessions.get(session_id)
        if session is not None:
            t_ms = clamp_current_time_ms(current_time_ms)
            allowed = effective_resource_scope(session.resource_scope, focus_resource_id)
            if allowed and self._pending_frontier(allowed, t_ms) is None:
                visual_through = self._visual_frontier(allowed, t_ms)
        resp = await self._chat_core(
            session_id=session_id,
            current_time_ms=current_time_ms,
            message=message,
            mode=mode,
            focus_resource_id=focus_resource_id,
            live_frame=live_frame,
            no_frame_note=no_frame_note,
            visual_through=visual_through,
        )
        if visual_through is not None:
            resp.visual_pending = True
            resp.visual_through_ms = visual_through
        return resp

    async def _chat_core(
        self,
        *,
        session_id: str,
        current_time_ms: int,
        message: str,
        mode: str = "qa",
        focus_resource_id: str | None = None,
        live_frame=None,
        no_frame_note: str | None = None,
        visual_through: int | None = None,
    ) -> ChatResponse:
        """Answer one learner question.

        ``live_frame`` (:class:`app.ingestion.liveframe.LiveFrame`, or ``None``)
        is the frame the learner actually captured, already described and already
        validated against the server-side duration by the route. It is injected
        as HIGH-PRIORITY context **alongside** the retrieved strict-T passages,
        never instead of them: a frame shows what is displayed, retrieval
        supplies the explanation from earlier audio, and the learner asked about
        both. Dropping retrieval would make "what does this say and what does it
        mean" unanswerable; dropping the frame would make "what am I looking at"
        answerable only up to the nearest 4-second sample.

        ``no_frame_note`` is prepended to the answer when a frame was asked for
        but the material has none (pdf/pptx/image). It is how that path stays
        honest without ever fabricating a frame.
        """
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
                answer=SCOPE_REFUSAL_TEXT,
                citations=[],
                refused=True,
                scope_empty=True,
                mode="grounded",
                grounded=False,
            )

        # Intent runs after the scope check (an unauthorised session still fails
        # closed) and before everything else. A greeting has no course answer:
        # sending it through retrieval produced a cited "answer" to "hi", and
        # sending it through the general-knowledge fallback produced an invented
        # lesson for "who are you". A captured frame always means a real
        # question about the screen, so it is never smalltalk.
        if self._agent is not None and self._pending_frontier(allowed, t_ms) is None:
            resp = await self._agent_answer(
                session=session,
                message=message,
                mode=mode,
                t_ms=t_ms,
                allowed=allowed,
                focus_resource_id=focus_resource_id,
                live_frame=live_frame,
                no_frame_note=no_frame_note,
            )
            if resp is not None:
                return resp

        intent = classify_intent(message) if live_frame is None else None
        if intent == SMALLTALK:
            history = self._messages.user_texts(session_id, limit=5)
            return await self._conversational_answer(session_id, message, history)
        # Study / overview requests ("what questions can the prof ask me") are
        # about the covered material as a whole. They take quiz / summarize
        # mode -- both strict-T and both barred from the general-knowledge
        # fallback -- unless the learner already picked a non-default mode.
        coverage_query = False
        if intent in INTENT_MODES and mode == "qa":
            mode = INTENT_MODES[intent]
            coverage_query = True

        pending = self._pending_frontier(allowed, t_ms)
        if pending is not None:
            self._persist(session_id, message, PROCESSING_PENDING_TEXT, [])
            return ChatResponse(
                answer=PROCESSING_PENDING_TEXT,
                citations=[],
                refused=True,
                mode="grounded",
                grounded=False,
                processing_pending=True,
                processed_through_ms=pending,
            )

        history = self._messages.user_texts(session_id, limit=5)
        # The literal meta-question has no semantic overlap with the lesson, so a
        # coverage request retrieves with a content query instead. Retrieval is
        # still strict-T gated and scope-intersected; only the query text and k
        # change.
        rewritten = COVERAGE_QUERY if coverage_query else rewrite_query(message, history)

        # Present-tense questions ("what is on screen right now?") ask about the
        # playhead, not about the course. Retrieval answers them from material
        # near T instead of the best-matching chunk anywhere in the gated
        # window. This is a preference INSIDE the already-gated pool, applied by
        # the store after the strict-T filter -- the gate itself is untouched.
        #
        # A frame question ("what am I looking at") is also present-tense even
        # when it names no recency marker, and its *explanation* comes from the
        # audio nearest the playhead. Adding it here is additive: every question
        # `is_recency_seeking` already claimed behaves exactly as before.
        prefer_recent = is_recency_seeking(message) or is_frame_question(message)

        retrieval = self._retrieval()
        passages = await retrieval.retrieve(
            rewritten,
            session.course_id,
            allowed,
            t_ms,
            k=12 if coverage_query else 8,
            focus_resource_id=focus_resource_id,
            prefer_recent=prefer_recent,
        )
        passages = apply_strict_timestamp(passages, t_ms, mode)

        live_passage = None
        if live_frame is not None:
            # The live frame is a passage at EXACTLY T: start_ms == end_ms == T.
            # `assert_live_frame_at` re-states the arithmetic that lets it pass
            # `end_ms <= T` with equality, so no gate needs a special case for
            # it. It goes FIRST because a captured frame is perfect evidence of
            # what is displayed right now, and retrieval stays behind it.
            live_passage = live_frame_passage(live_frame)
            assert_live_frame_at(live_passage, t_ms)
            passages = [live_passage, *passages]

        prefix = f"{no_frame_note}\n\n" if no_frame_note else ""
        state = "live" if live_passage is not None else ("document" if no_frame_note else None)

        # --- Mode selection (see prompts.SYSTEM_BASE for the register) --------
        #
        # GROUNDED is the default and the product promise. SUPPLEMENTAL happens
        # on exactly two triggers, both narrow:
        #
        #   1. the learner EXPLICITLY asked to go beyond the lesson
        #      (``is_supplemental_request``); the grounded attempt is skipped
        #      entirely, because the answer they asked for is by definition not
        #      the one in the video.
        #   2. the GROUNDED attempt returned the refusal verdict -- the model
        #      read the passages and concluded they do not cover the question.
        #      This is the "the video has not got here yet" signal.
        #
        # What is deliberately NOT a trigger: a citation-validation failure
        # (unknown or missing id). That is a grounding *defect*, not evidence of
        # an uncovered question, and answering it with un-cited general
        # knowledge would launder a broken answer into a plausible one. It still
        # refuses.
        #
        # Neither trigger touches retrieval. Supplemental mode adds NO passages
        # and relaxes NO filter; it produces ZERO citations instead.
        # A message with no subject concept in it ("can you?", "and you?") has
        # nothing to teach: the fallback would have to invent a topic, which is
        # how "who are you" once became an unrequested Chain Rule lesson.
        mode_allows_supplemental = (
            mode not in SUPPLEMENTAL_DISABLED_MODES and has_subject_content(message)
        )
        explicit_supplemental = mode_allows_supplemental and is_supplemental_request(message)

        visual_lag = visual_through is not None
        low_text = VISUAL_PENDING_REFUSAL_TEXT if visual_lag else REFUSAL_TEXT

        if not passages:
            if (
                mode_allows_supplemental
                and not visual_lag
                and not (prefer_recent or is_frame_question(message))
            ):
                resp = await self._supplemental_answer(
                    session_id=session_id,
                    message=message,
                    passages=[],
                    mode=mode,
                    history=history,
                    prefix=prefix,
                    state=state,
                )
                if resp is not None:
                    return resp
            self._persist(session_id, message, prefix + low_text, [])
            return ChatResponse(
                answer=prefix + low_text,
                citations=[],
                refused=True,
                mode="grounded",
                grounded=False,
            )

        if explicit_supplemental:
            resp = await self._supplemental_answer(
                session_id=session_id,
                message=message,
                passages=passages,
                mode=mode,
                history=history,
                prefix=prefix,
                state=state,
            )
            if resp is not None:
                return resp

        system, user_prompt = build_grounded_prompt(
            message if coverage_query else rewritten, passages, mode=mode, history=history
        )
        result = await self._llm.complete(
            system=system, user_prompt=user_prompt, passages=passages, mode=mode
        )

        def refuse(low_evidence: bool = False) -> ChatResponse:
            text = prefix + (low_text if low_evidence else REFUSAL_TEXT)
            self._persist(session_id, message, text, [])
            return ChatResponse(
                answer=text,
                citations=[],
                refused=True,
                live_frame=state,
                mode="grounded",
                grounded=False,
            )

        # A refusal answer carries no citations by design.
        if result.text == REFUSAL_TEXT:
            if (
                mode_allows_supplemental
                and not visual_lag
                and not (prefer_recent or is_frame_question(message))
            ):
                resp = await self._supplemental_answer(
                    session_id=session_id,
                    message=message,
                    passages=passages,
                    mode=mode,
                    history=history,
                    prefix=prefix,
                    state=state,
                )
                if resp is not None:
                    return resp
            return refuse(low_evidence=True)

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
            return refuse()

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
                return refuse(low_evidence=True)
            cited_ids = list(result.cited_ids) or extract_cited_ids(result.text)
            for cid in extract_cited_ids(result.text):
                if cid not in cited_ids:
                    cited_ids.append(cid)
            unknown = [c for c in cited_ids if c not in allowed_ids]
            if unknown:
                log.warning("unknown citation(s) rejected after repair: %s", unknown)
                return refuse()

        if not cited_ids:
            log.warning(
                "uncited_answer: non-refusal answer with empty cited_ids refused"
            )
            return refuse()

        # A citation-validation failure above is a grounding DEFECT, not a
        # "not covered yet" signal, so it never escalates to supplemental.

        by_id = {p.chunk_id: p for p in passages}
        # Validation above already ran against every distinct cited id.
        # Every validated citation is surfaced individually, in cited order:
        # overlapping spans from sliding transcript windows are all shown so the
        # learner can see every supporting chunk.
        cited_passages = [by_id[c] for c in cited_ids if c in by_id]
        citations = [_passage_to_citation(p) for p in cited_passages]
        # Validation above ran against the raw text, so it is now safe to drop
        # the transport markers: the UI renders badges from `citations[]`.
        answer = prefix + strip_citation_markers(result.text)
        self._persist(session_id, message, answer, [c.model_dump() for c in citations])
        return ChatResponse(
            answer=answer,
            citations=citations,
            refused=False,
            live_frame=state,
            mode="grounded",
            grounded=True,
        )

    async def _supplemental_answer(
        self,
        *,
        session_id: str,
        message: str,
        passages: Sequence,
        mode: str,
        history: Sequence[str],
        prefix: str,
        state: str | None,
    ) -> ChatResponse | None:
        """Teach an uncovered question from first principles, labelled, uncited.

        Returns ``None`` when the provider declines (or is unavailable), so the
        caller falls back to the ordinary grounded refusal rather than emitting
        an empty answer. A refusal is always a better failure than a blank.

        Two invariants hold here by construction, not by prompt instruction
        alone:

        * **Zero citations.** The answer is built with ``citations=[]`` and every
          ``[chunk_id=...]`` marker is stripped from the text, so a model that
          ignores the instruction cannot smuggle a video citation into an answer
          that is explicitly labelled as not coming from the video.
        * **The gate is untouched.** ``passages`` are the ALREADY-GATED covered
          set, used only for orientation ("the lesson has got this far"). No
          retrieval runs here, no filter is widened, and nothing after T can
          appear in the prompt even to be summarised.
        """
        system, user_prompt = build_supplemental_prompt(
            message, passages, mode=mode, history=history
        )
        # ``passages`` is passed through for provider parity only; no provider
        # in this repo reads it for content, and the prompt carries everything.
        result = await self._llm.complete(
            system=system, user_prompt=user_prompt, passages=list(passages), mode=mode
        )
        text = (result.text or "").strip()
        if not text or text == REFUSAL_TEXT:
            log.info("supplemental declined; falling back to the grounded refusal")
            return None

        # Strip markers unconditionally: this answer claims no video provenance,
        # so a marker would be false however valid the id is.
        answer = prefix + strip_citation_markers(text)
        self._persist(session_id, message, answer, [])
        log.info("supplemental answer served (zero citations, mode=supplemental)")
        return ChatResponse(
            answer=answer,
            citations=[],
            refused=False,
            live_frame=state,
            mode="supplemental",
            grounded=False,
        )

    async def _agent_answer(
        self,
        *,
        session,
        message: str,
        mode: str,
        t_ms: int,
        allowed: list[str],
        focus_resource_id: str | None,
        live_frame,
        no_frame_note: str | None,
    ) -> ChatResponse | None:
        """One agentic turn, or ``None`` to fall back to the classic pipeline.

        The agent only ever reads through time-gated, scope-intersected tools,
        and :meth:`LessonAgent._validate` has already checked every cited id
        against what those tools returned. Any provider failure or unsafe
        answer surfaces here as ``None``.
        """
        from app.chat.agent import AgentUnavailableError

        live_passage = None
        if live_frame is not None:
            live_passage = live_frame_passage(live_frame)
            assert_live_frame_at(live_passage, t_ms)
        history = self._messages.user_texts(session.id, limit=5)
        from app.config import settings

        try:
            # One wall-clock budget for the whole tool loop: a queued free-tier
            # endpoint must degrade to the classic answer, not hang the learner.
            outcome = await asyncio.wait_for(
                self._agent.run(
                    message=message,
                    mode=mode,
                    t_ms=t_ms,
                    course_id=session.course_id,
                    allowed=allowed,
                    focus_resource_id=focus_resource_id,
                    store=self._store,
                    retrieval=self._retrieval(),
                    history=history,
                    live_passage=live_passage,
                ),
                timeout=settings.agent_total_timeout_s,
            )
        except (AgentUnavailableError, TimeoutError) as exc:
            log.warning("agent unavailable (%r); using the classic pipeline", exc)
            return None

        prefix = f"{no_frame_note}\n\n" if no_frame_note else ""
        state = "live" if live_passage is not None else ("document" if no_frame_note else None)
        if outcome.kind == "refused":
            text = prefix + REFUSAL_TEXT
            self._persist(session.id, message, text, [])
            return ChatResponse(
                answer=text, citations=[], refused=True, live_frame=state,
                mode="grounded", grounded=False,
            )
        # Dedupe in cited order; citations come only from validated passages.
        cited = list({p.chunk_id: p for p in outcome.cited}.values())
        citations = [_passage_to_citation(p) for p in cited]
        answer = prefix + strip_citation_markers(outcome.text)
        self._persist(session.id, message, answer, [c.model_dump() for c in citations])
        log.info(
            "agent answer served (kind=%s, tool_calls=%d, citations=%d)",
            outcome.kind, outcome.tool_calls, len(citations),
        )
        return ChatResponse(
            answer=answer,
            citations=citations,
            refused=False,
            live_frame=state,
            mode=outcome.kind,
            grounded=outcome.kind == "grounded",
        )

    async def _conversational_answer(
        self, session_id: str, message: str, history: Sequence[str]
    ) -> ChatResponse:
        """Reply to a greeting / thanks / "who are you": no retrieval, no citations.

        Nothing about the lesson is claimed, so there is nothing to ground. Any
        marker the model emits anyway is stripped, and a declined or failed
        provider call falls back to a fixed introduction rather than an error.
        """
        system, user_prompt = build_conversational_prompt(message, history)
        try:
            result = await self._llm.complete(
                system=system, user_prompt=user_prompt, passages=[], mode="qa"
            )
            text = strip_citation_markers((result.text or "").strip())
        except Exception:  # noqa: BLE001 -- any provider error; a greeting must not fail
            log.warning("conversational reply failed; using the fixed introduction")
            text = ""
        if not text or text == REFUSAL_TEXT:
            text = CONVERSATIONAL_FALLBACK_TEXT
        self._persist(session_id, message, text, [])
        return ChatResponse(
            answer=text,
            citations=[],
            refused=False,
            mode="conversational",
            grounded=False,
        )

    def _persist(
        self, session_id: str, user_text: str, answer: str, citations: list[dict]
    ) -> None:
        self._messages.append(session_id, "user", user_text, [])
        self._messages.append(session_id, "assistant", answer, citations)
