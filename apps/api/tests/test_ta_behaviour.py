"""Teaching-assistant BEHAVIOUR: the two modes, and forward-looking suppression.

This file is deliberately separate from ``test_chat_safety.py``. That file pins
the invariants (strict-T gate, citation validation, scope intersection) and its
refusals are CORRECT for the questions it asks. This file pins the behaviour
that changed: a learner who asks something the lecture has not reached gets a
useful, clearly-labelled answer with ZERO citations instead of a bare refusal,
and the lecture's not-yet-reached conclusion is not spoiled.

Two rules govern everything below.

1. **No existing test was weakened to get here.** 276 passed before this file
   existed and 276 + these pass after it. Where an old test would have pinned
   the OLD behaviour, it asked about a present-tense / frame question -- for
   which "not from the video" is not an answer -- so the two behaviours do not
   actually conflict.
2. **Mode is a PROVENANCE claim, so it is enforced in code, not in a prompt.**
   A supplemental answer is constructed with ``citations=[]`` and has its
   ``[chunk_id=...]`` markers stripped, whatever the model emitted. The prompt
   asks for no citations; the service guarantees it.

Anti-spoiler is prompt-level by necessity and the tests below are honest about
it: they pin the *contract* (the classifier fires on result-seeking questions,
the rule reaches the prompt, and a compliant model then withholds the result).
They cannot pin a guarantee about an arbitrary model's compliance -- no test can
-- which is exactly why the strict-T gate, the part that CAN be guaranteed, is
enforced structurally and left untouched.
"""

from __future__ import annotations

import asyncio
import functools
from dataclasses import dataclass, field

import pytest

from app.chat.llm import LLMResult
from app.chat.prompts import (
    ANTI_SPOILER_RULE,
    FORWARD_SUPPRESSION_RULE,
    REFUSAL_TEXT,
    RETRIEVAL_SPEAK_PHRASES,
    build_grounded_prompt,
    build_supplemental_prompt,
    is_forward_looking_result,
    system_for_mode,
)
from app.chat.service import ChatService
from app.chat.sessions import InMemoryMessageStore, InMemorySessionStore
from app.retrieval.recency import is_supplemental_request

# ``Partial-derivatives.mp4`` runs 655.5s (see docs/HANDOFF.md section 1).
TEN_MIN_MS = 10 * 60 * 1000
TWENTY_MIN_MS = 20 * 60 * 1000
THIRTY_MIN_MS = 30 * 60 * 1000

RESOURCE = "res_video"


@dataclass
class FakeChunk:
    """A retrieved passage, shaped exactly as ``app/chat`` reads one."""

    chunk_id: str
    resource_id: str
    source_type: str
    text: str
    kind: str | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    video_id: str | None = None
    page_no: int | None = None
    slide_no: int | None = None
    score: float = 1.0
    metadata: dict = field(default_factory=dict)


def _video(chunk_id: str, start_ms: int, end_ms: int, text: str | None = None):
    return FakeChunk(
        chunk_id=chunk_id,
        resource_id=RESOURCE,
        source_type="video",
        kind="transcript",
        text=text or f"spoken transcript {chunk_id}",
        start_ms=start_ms,
        end_ms=end_ms,
        video_id="vid_pderiv",
    )


class ScriptedLLM:
    """Returns queued results, recording every call's system + user prompt."""

    def __init__(self, *results: LLMResult, repeat_last: bool = True) -> None:
        self._results = list(results)
        self._repeat_last = repeat_last
        self.calls: list[dict] = []

    async def complete(self, *, system, user_prompt, passages, mode) -> LLMResult:
        self.calls.append(
            {
                "system": system,
                "user_prompt": user_prompt,
                "mode": mode,
                "n_passages": len(passages),
            }
        )
        if not self._results:
            return LLMResult(text=REFUSAL_TEXT, cited_ids=[])
        idx = min(len(self.calls) - 1, len(self._results) - 1)
        return self._results[idx]


class SupplementalThenGroundedLLM:
    """Refuses the grounded turn, teaches on the supplemental turn.

    Models the real failure the change is for: the grounded attempt concludes
    the passages do not cover the question, and the service then asks again
    under the supplemental contract.
    """

    def __init__(self, supplemental_text: str) -> None:
        self.supplemental_text = supplemental_text
        self.calls: list[dict] = []

    async def complete(self, *, system, user_prompt, passages, mode) -> LLMResult:
        from app.chat.llm import SUPPLEMENTAL_SYSTEM_MARKER

        self.calls.append(
            {
                "system": system,
                "user_prompt": user_prompt,
                "passage_ids": [p.chunk_id for p in passages],
            }
        )
        if SUPPLEMENTAL_SYSTEM_MARKER in system:
            return LLMResult(text=self.supplemental_text, cited_ids=[])
        return LLMResult(text=REFUSAL_TEXT, cited_ids=[])


class CompliantLLM:
    """Obeys whichever contract it is handed.

    Used for the anti-spoiler tests: it states the late result only when the
    prompt has NOT been given the suppression rule. That makes the end-to-end
    plumbing testable -- if the rule stops reaching the prompt, the leak comes
    back and the test fails.
    """

    FUTURE_ANSWER = "2xy"
    RESULT_Q = "what does dy come out to?"

    async def complete(self, *, system, user_prompt, passages, mode) -> LLMResult:
        from app.chat.llm import SUPPLEMENTAL_SYSTEM_MARKER

        if SUPPLEMENTAL_SYSTEM_MARKER in system:
            return LLMResult(text="Not from the video.", cited_ids=[])
        if FORWARD_SUPPRESSION_RULE in user_prompt:
            return LLMResult(
                text=(
                    "The lesson has not got to that result yet at this point. "
                    "So far it has set up the derivative of f(x,y) with respect "
                    f"to x. [chunk_id={passages[0].chunk_id}]"
                ),
                cited_ids=[passages[0].chunk_id],
            )
        if self.RESULT_Q in user_prompt:
            return LLMResult(
                text=f"It comes out to {self.FUTURE_ANSWER}.",
                cited_ids=[passages[0].chunk_id],
            )
        return LLMResult(
            text=f"Setup [chunk_id={passages[0].chunk_id}].",
            cited_ids=[passages[0].chunk_id],
        )


def sync_test(fn):
    """Run an async body on a private loop (``pytest-asyncio`` is not a dep)."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))

    return wrapper


@pytest.fixture
def chat_env(monkeypatch):
    """Factory: a real ``ChatService`` over a fake, gate-free retrieval seam."""

    def _make(chunks, llm=None):
        sessions = InMemorySessionStore()
        messages = InMemoryMessageStore()
        store = _FakeStore(list(chunks))
        provider = llm if llm is not None else ScriptedLLM()
        service = ChatService(
            store=store,
            embedder=None,
            llm=provider,
            sessions=sessions,
            messages=messages,
        )
        session = sessions.create(
            learner_id="learner_1",
            course_id="course_1",
            resource_id=RESOURCE,
            lesson_id="lesson_1",
            resource_scope=[RESOURCE],
            current_time_ms=TWENTY_MIN_MS,
        )
        monkeypatch.setattr(
            ChatService, "_retrieval", lambda self: _FakeRetrieval(store), raising=True
        )
        return service, session.id, provider

    return _make


class _FakeStore:
    def __init__(self, chunks):
        self.chunks = list(chunks)

    def in_scope(self, resource_scope, focus_resource_id=None):
        granted = set(resource_scope or [])
        rows = [c for c in self.chunks if c.resource_id in granted]
        if focus_resource_id:
            rows = [c for c in rows if c.resource_id == focus_resource_id]
        return rows


class _FakeRetrieval:
    """No ranking, no embeddings -- and deliberately NO timestamp gate.

    Mirrors the existing safety-test double so the gate under test is always
    ``apply_strict_timestamp`` in the service, never a fake.
    """

    def __init__(self, store):
        self._store = store
        self.calls: list[dict] = []

    async def retrieve(
        self,
        query,
        course_id,
        resource_scope,
        current_time_ms,
        k=8,
        focus_resource_id=None,
        prefer_recent=False,
    ):
        self.calls.append({"query": query, "current_time_ms": current_time_ms})
        return self._store.in_scope(resource_scope, focus_resource_id)[:k]


# ---------------------------------------------------------------------------
# 1. Grounded is still the default
# ---------------------------------------------------------------------------


@sync_test
async def test_grounded_mode_is_still_the_default(chat_env):
    """An ordinary, covered question is GROUNDED, cited, and takes no fallback.

    The whole point of the two-mode design is that adding a fallback did not
    make it the common path. Three independent checks:

    * the response declares ``mode == "grounded"`` and ``grounded is True``;
    * it carries the validated citation;
    * the provider was called ONCE -- no supplemental retry happened even
      though a retry was available.
    """
    passage = _video("c_concept", 4_000, 10_000, "a partial derivative holds y")
    llm = ScriptedLLM(
        LLMResult(
            text="A partial derivative holds one variable fixed [chunk_id=c_concept].",
            cited_ids=["c_concept"],
        )
    )
    service, sid, provider = chat_env([passage], llm=llm)

    resp = await service.chat(
        session_id=sid,
        current_time_ms=TWENTY_MIN_MS,
        message="what is a partial derivative",
        mode="qa",
    )

    assert resp.mode == "grounded"
    assert resp.grounded is True
    assert resp.refused is False
    assert [c.chunk_id for c in resp.citations] == ["c_concept"]
    assert len(provider.calls) == 1, "a covered question must not trigger a fallback"


def test_grounded_is_the_schema_default():
    """An unstated mode reads as grounded, so old clients cannot mis-render.

    A frontend that ignores both new fields and keys off ``refused``/
    ``citations`` keeps working; one that reads ``mode`` sees the product's
    default even for a refusal it did not construct.
    """
    from app.chat.schemas import ChatResponse

    bare = ChatResponse(answer="x")
    assert bare.mode == "grounded"
    assert bare.grounded is True


# ---------------------------------------------------------------------------
# 2. Uncovered question -> useful, labelled, uncited
# ---------------------------------------------------------------------------

SUPPLEMENTAL_REPLY = (
    "The lesson has not reached the chain rule yet at this point.\n\n"
    "**Not from the video** - general knowledge:\n\n"
    "The chain rule says that when one function is composed with another, "
    "you differentiate the outside function, keep its argument intact, and "
    "multiply by the derivative of the inside function:\n\n"
    "$$\\frac{d}{dx}f(g(x)) = f'(g(x))\\cdot g'(x)$$\n\n"
    "For $f(x,y) = g(h(x,y))$, hold $y$ constant first."
)


@sync_test
async def test_uncovered_question_is_answered_usefully_and_labelled_not_from_video(
    chat_env,
):
    """The core behaviour change: no more bare refusal for an uncovered question.

    The learner asks about a topic the lecture has not got to. Grounded returns
    the refusal verdict; the service must then teach it from first principles
    AND say plainly that the lesson has not covered it AND mark the explanation
    as not coming from the video.

    The "worth reading" part is asserted as content, not as a keyword: the
    answer contains a definition-shaped explanation and a correct LaTeX
    statement of the rule. An answer that merely said "not covered yet" would
    fail this test, which is the failure the user actually reported.
    """
    covered = _video("c_early", 4_000, 10_000, "a partial derivative holds y")
    llm = SupplementalThenGroundedLLM(SUPPLEMENTAL_REPLY)
    service, sid, provider = chat_env([covered], llm=llm)

    resp = await service.chat(
        session_id=sid,
        current_time_ms=TWENTY_MIN_MS,
        message="what is the chain rule?",
        mode="qa",
    )

    assert resp.refused is False
    assert resp.mode == "supplemental"
    assert resp.grounded is False
    # Not the old behaviour.
    assert resp.answer != REFUSAL_TEXT
    # Labelled, in the learner's own reading order: "not covered yet" FIRST,
    # then the label on the teaching part.
    assert "has not reached" in resp.answer
    assert "Not from the video" in resp.answer
    # Useful: real maths, not a padded paragraph about being unable to help.
    assert "chain rule" in resp.answer.lower()
    assert "$$\\frac{d}{dx}" in resp.answer
    # Two turns: the grounded refusal, then the supplemental teach.
    assert len(provider.calls) == 2
    assert provider.calls[0]["passage_ids"] == ["c_early"]
    assert provider.calls[1]["passage_ids"] == ["c_early"], (
        "supplemental must not retrieve anything new -- only the already-gated "
        "covered passages, for orientation"
    )


@sync_test
async def test_supplemental_answer_carries_zero_citations(chat_env):
    """Zero citations, and no marker survives in the text either.

    A supplemental answer asserts a provenance change ("not from the video"), so
    a video citation would be a lie about where the content came from. Asserted
    at both layers: the ``citations`` list is empty, and the visible answer text
    has been stripped of every ``[chunk_id=...]`` marker.

    The model here deliberately VIOLATES the instruction and cites anyway. That
    is the point: the guarantee must hold when the provider misbehaves, not only
    when it complies. The cited id is a REAL one from the retrieved set, so the
    grounded validation path would have accepted it -- the strip is what stops
    it.
    """
    covered = _video("c_early", 4_000, 10_000)
    leaky = SupplementalThenGroundedLLM(
        "The lesson has not got to that yet.\n\n"
        "**Not from the video** - general knowledge: the rule is $\\partial$ "
        "composition [chunk_id=c_early]."
    )
    service, sid, _ = chat_env([covered], llm=leaky)

    resp = await service.chat(
        session_id=sid,
        current_time_ms=TWENTY_MIN_MS,
        message="what is the chain rule?",
        mode="qa",
    )

    assert resp.mode == "supplemental"
    assert resp.citations == []
    assert "chunk_id" not in resp.answer
    assert "[chunk_id=" not in resp.answer
    # The substantive content is still served.
    assert "general knowledge" in resp.answer


@sync_test
async def test_citation_validation_defect_does_not_become_a_fallback(chat_env):
    """An UNKNOWN id is a grounding defect and must still refuse.

    Without this, the new fallback would launder every grounding bug into a
    plausible uncited answer: the model cites nothing valid, and instead of a
    refusal the learner gets unsourced general knowledge presented as help.
    Only an explicit REFUSAL verdict escalates to supplemental.
    """
    passage = _video("c_real", 4_000, 10_000)
    llm = ScriptedLLM(
        LLMResult(
            text="A partial derivative [chunk_id=c_invented].", cited_ids=["c_invented"]
        )
    )
    service, sid, _ = chat_env([passage], llm=llm)

    resp = await service.chat(
        session_id=sid,
        current_time_ms=TWENTY_MIN_MS,
        message="what is a partial derivative",
        mode="qa",
    )

    assert resp.refused is True
    assert resp.answer == REFUSAL_TEXT
    assert resp.citations == []
    assert resp.mode == "grounded"


# ---------------------------------------------------------------------------
# 3. Grounded still validates, and never exceeds T
# ---------------------------------------------------------------------------


@sync_test
async def test_grounded_answer_still_carries_validated_citations(chat_env):
    """The product promise: grounded answers are cited and validated.

    Complementary to the previous test. Together they pin both halves of the
    contract -- a bad grounded answer is refused, a good grounded answer is
    served with its validated citation.
    """
    a = _video("c_a", 4_000, 10_000)
    b = _video("c_b", 20_000, 26_000)
    llm = ScriptedLLM(
        LLMResult(
            text="It holds one variable fixed [chunk_id=c_a] [chunk_id=c_b].",
            cited_ids=["c_a", "c_b"],
        )
    )
    service, sid, _ = chat_env([a, b], llm=llm)

    resp = await service.chat(
        session_id=sid,
        current_time_ms=TWENTY_MIN_MS,
        message="what does it mean to hold y constant",
        mode="qa",
    )

    assert resp.mode == "grounded"
    assert [c.chunk_id for c in resp.citations] == ["c_a", "c_b"]
    assert all(c.modality == "transcript" for c in resp.citations)
    # Markers are transport, not content.
    assert "[chunk_id=" not in resp.answer


@pytest.mark.parametrize(
    "t_ms",
    [30_000, 120_000, 300_000, 480_000, 655_000],
    ids=["t30s", "t2m", "t5m", "t8m", "tend"],
)
@sync_test
async def test_grounded_answer_never_exceeds_t(chat_env, t_ms):
    """At every T, no served citation ends after T.

    The fake retrieval is deliberately UNGATED (it returns every chunk in
    scope), so ``apply_strict_timestamp`` in the service is the only thing
    between a future chunk and the answer. The model below cites the chunk with
    the largest ``end_ms`` it was handed -- the one a non-compliant prompt
    would point it at -- so if anything leaked, this fails rather than being
    masked by a cooperative double.
    """
    chunks = [
        _video("c_early", 8_000, 12_000),
        _video("c_mid", 300_000, 304_000),
        _video("c_late", 640_000, 655_000),
    ]
    llm = _CitesLatestPassageLLM()
    service, sid, _ = chat_env(chunks, llm=llm)

    resp = await service.chat(
        session_id=sid,
        current_time_ms=t_ms,
        message="what is covered so far?",
        mode="summarize",
    )

    for c in resp.citations:
        assert c.end_ms is not None
        assert c.end_ms <= t_ms, f"{c.chunk_id} ends at {c.end_ms} > T={t_ms}"
    if resp.citations:
        assert max(c.end_ms for c in resp.citations) <= t_ms
    else:
        # Legal outcome (strict mode dropped everything eligible for citation):
        # it must be an honest refusal, never a partial serve.
        assert resp.refused is True


class _CitesLatestPassageLLM:
    """Cites the passage with the largest ``end_ms`` it was given."""

    def __init__(self):
        self.calls: list[dict] = []

    async def complete(self, *, system, user_prompt, passages, mode) -> LLMResult:
        from app.chat.llm import SUPPLEMENTAL_SYSTEM_MARKER

        self.calls.append({"n": len(passages)})
        if SUPPLEMENTAL_SYSTEM_MARKER in system or not passages:
            return LLMResult(text=REFUSAL_TEXT, cited_ids=[])
        best = max(passages, key=lambda p: (p.end_ms or -1, p.chunk_id))
        return LLMResult(
            text=f"Covers it [chunk_id={best.chunk_id}].", cited_ids=[best.chunk_id]
        )


# ---------------------------------------------------------------------------
# 4. Forward-looking suppression (anti-spoiler)
# ---------------------------------------------------------------------------


def test_forward_looking_classifier_is_narrow_and_bidirectional():
    """The classifier must fire on results-seeking and NOT on definitions.

    The second half is the anti-over-refusal half, and it is the half that is
    easy to break: a classifier that also matches "what is a partial
    derivative" would inject the suppression rule into every early question and
    the tutor would start answering 0:30 with lecture-safety notices.
    """
    for phrase in (
        "what does dy come out to?",
        "what is the final answer?",
        "what do we get at the end?",
        "what will happen next?",
        "why does it turn out to be 2xy?",
        "what is the exact result",
        "so what is dy",
        "tell me the conclusion",
    ):
        assert is_forward_looking_result(phrase) is True, phrase

    for phrase in (
        "what is a partial derivative",
        "what does it mean to hold y constant",
        "what am I looking at right now?",
        "what equation is on screen?",
        "explain the difference between dx and dy",
        "is this partial derivatives?",
        "what does d/dx mean",
    ):
        assert is_forward_looking_result(phrase) is False, phrase


def test_suppression_reaches_the_prompt_only_for_result_questions():
    """The rule is injected into the user prompt, conditionally.

    Prompt-level because a retrieval filter cannot do this job: the conclusion
    the learner is asking for is stated by NO passage (it is in the future), so
    there is nothing to filter out. It is a decision about what the model is
    about to WRITE.
    """
    passage = _video("c_a", 4_000, 10_000)

    _, result_prompt = build_grounded_prompt("what does dy come out to?", [passage])
    assert FORWARD_SUPPRESSION_RULE in result_prompt
    # ...and it must actually forbid the spoiler, not merely be present.
    assert "must not supply it from general knowledge" in FORWARD_SUPPRESSION_RULE
    assert "Do NOT state the value" in FORWARD_SUPPRESSION_RULE

    _, plain_prompt = build_grounded_prompt("what is a partial derivative", [passage])
    assert FORWARD_SUPPRESSION_RULE not in plain_prompt


def test_system_prompt_carries_the_anti_spoiler_rule_with_its_carve_out():
    """The rule ships in every mode, and it states when NOT to apply.

    The carve-out ("Scope this rule to results, never to ordinary questions")
    is the anti-over-refusal guard. Without it the rule reads as "decline
    anything you cannot see proved", which is how a tutor becomes useless.
    """
    for mode in ("qa", "explain_simple", "analogy", "compare", "summarize", "quiz"):
        system = system_for_mode(mode)
        assert ANTI_SPOILER_RULE in system, mode
    assert "never to ordinary questions" in ANTI_SPOILER_RULE
    assert "answered in full, exactly as before" in ANTI_SPOILER_RULE


@sync_test
async def test_future_conclusion_is_not_spoiled(chat_env):
    """The late result is withheld when the question asks for it.

    ``CompliantLLM`` states the spoiler only when the suppression rule is
    ABSENT from the prompt, so this is a real end-to-end test of the plumbing:
    regress the rule's injection and the leak returns and this fails.

    ``CompliantLLM`` is honest about its limits -- it proves the rule reaches a
    cooperating model and that the model then withholds the result. It cannot
    prove compliance of an arbitrary provider. The part that CAN be guaranteed
    structurally is the strict-T gate, pinned in ``test_chat_safety.py`` and
    untouched here.
    """
    covered = _video("c_setup", 4_000, 10_000, "the lecture defines f(x,y)")
    service, sid, _ = chat_env([covered], llm=CompliantLLM())

    resp = await service.chat(
        session_id=sid,
        current_time_ms=30_000,  # the lecture has NOT reached the result
        message=CompliantLLM.RESULT_Q,
        mode="qa",
    )

    assert CompliantLLM.FUTURE_ANSWER not in resp.answer
    assert resp.mode == "grounded"
    # What it SHOULD give instead: honest, cited, and about the groundwork.
    assert resp.refused is False
    assert [c.chunk_id for c in resp.citations] == ["c_setup"]
    assert "has not got to that result yet" in resp.answer


@sync_test
async def test_supplemental_prompt_also_forbids_foreshadowing(chat_env):
    """Foreshadowing is forbidden in BOTH modes.

    The fallback is where it is most tempting: the model has general knowledge
    of the exact result and is being asked to be helpful. A supplemental answer
    that says "...which is where you get 2xy" has spoiled the lecture as surely
    as a grounded one that does.
    """
    system, user = build_supplemental_prompt(
        "what does dy come out to?", [_video("c_a", 4_000, 10_000)]
    )
    # Both halves of the contract must reach the model, not just the system
    # prompt: a provider that only sees the user turn still gets the rule.
    assert "do not state, predict, hint at or foreshadow" in user.lower()
    assert "Do not write any [chunk_id=...] marker" in system
    assert "Do not state, predict, hint at or foreshadow any result" in system
    assert "Emit no citation markers" in user


# ---------------------------------------------------------------------------
# 5. Anti-over-refusal
# ---------------------------------------------------------------------------


@sync_test
async def test_simple_question_at_early_t_still_gets_a_useful_answer(chat_env):
    """"what is a partial derivative?" at T=30s must be ANSWERED, not declined.

    The regression this guards is the one the anti-spoiler work could cause: a
    tutor so careful about not spoiling the lesson that it refuses to define a
    term the learner has already been shown. The rule is scoped to RESULTS, and
    this test holds that scope in place at exactly the timestamp where a
    leak-prone rule would bite.

    Also asserts the grounded attempt never escalated to supplemental: a
    definition question has support, so it must be cited and grounded.
    """
    covered = _video("c_def", 8_000, 12_000, "a partial derivative holds y constant")
    llm = ScriptedLLM(
        LLMResult(
            text="It is the derivative with respect to one variable while the "
            "others are held constant [chunk_id=c_def].",
            cited_ids=["c_def"],
        )
    )
    service, sid, provider = chat_env([covered], llm=llm)

    resp = await service.chat(
        session_id=sid,
        current_time_ms=30_000,
        message="what is a partial derivative",
        mode="qa",
    )

    assert resp.refused is False
    assert resp.mode == "grounded"
    assert resp.grounded is True
    assert [c.chunk_id for c in resp.citations] == ["c_def"]
    assert len(provider.calls) == 1
    # The prompt must not have been told to withhold a result here.
    _, prompt = build_grounded_prompt("what is a partial derivative", [covered])
    assert FORWARD_SUPPRESSION_RULE not in prompt


@sync_test
async def test_frame_question_never_falls_back_to_general_knowledge(chat_env):
    """"what am I looking at right now?" has no general-knowledge answer.

    The fallback must not become a way to narrate what the video is about to
    show -- that is the spoiler, arriving through the front door. So a
    frame/present-tense question refuses even when the learner ALSO asks for
    outside help.
    """
    covered = _video("c_frame", 4_000, 10_000)
    llm = SupplementalThenGroundedLLM(
        "**Not from the video** - general knowledge: usually a partial derivative."
    )
    service, sid, provider = chat_env([covered], llm=llm)

    resp = await service.chat(
        session_id=sid,
        current_time_ms=TWENTY_MIN_MS,
        message=(
            "what am I looking at right now? and if it is not on screen, "
            "explain it from general knowledge"
        ),
        mode="qa",
    )

    assert is_supplemental_request(
        "what am I looking at right now? and if it is not on screen, "
        "explain it from general knowledge"
    ) is False
    assert resp.mode == "grounded"
    assert len(provider.calls) == 1, "no supplemental turn for a frame question"


@sync_test
async def test_supplemental_modes_still_refuse(chat_env):
    """``summarize``/``quiz`` are about the covered material: no fallback.

    "General knowledge" contributes nothing to a summary of what has been
    watched, so escalating there would answer a different question than the one
    asked. Pinned because the mode set is an easy place to be over-broad.
    """
    covered = _video("c_covered", 4_000, 10_000)
    llm = SupplementalThenGroundedLLM("**Not from the video**: general knowledge.")
    service, sid, _ = chat_env([covered], llm=llm)

    resp = await service.chat(
        session_id=sid,
        current_time_ms=TWENTY_MIN_MS,
        message="summarize what we covered",
        mode="summarize",
    )

    assert resp.refused is True
    assert resp.answer == REFUSAL_TEXT
    assert resp.mode == "grounded"


def test_explicit_supplemental_request_is_recognised_and_narrow():
    """The explicit opt-in is a phrase list, not a guess.

    A false positive silently converts a grounded answer into an uncited one and
    removes the learner's ability to check the claim -- worse than a refusal. So
    both directions are pinned.
    """
    for phrase in (
        "explain beyond the video",
        "from general knowledge, how does the chain rule work?",
        "if the video doesn't cover it, explain anyway",
        "tell me this from general knowledge",
        "I need help from outside the lecture",
        "teach me this from scratch",
        "what does the textbook say about this",
    ):
        assert is_supplemental_request(phrase) is True, phrase

    for phrase in (
        "what is a partial derivative",
        "what does the video say so far",
        "what am I looking at right now?",
        "summarize what we covered",
        "quiz me on the chain rule",
        "what equation is on screen?",
        "explain the chain rule",
        "",
    ):
        assert is_supplemental_request(phrase) is False, phrase


# ---------------------------------------------------------------------------
# 6. Register: no retrieval-speak
# ---------------------------------------------------------------------------


def test_no_retrieval_speak_in_prompt_contract():
    """No prompt may instruct the model to narrate its sources.

    The register failure was never an instruction to be chatty -- it was the
    ABSENCE of an instruction to stop talking like an index. So the contract is
    asserted as data:

    * every known retrieval-speak phrase appears NOWHERE in any assembled
      prompt, in ANY mode. It must be absent from the prohibition too, which is
      why the rule talks about "your sources" instead of quoting the phrases.
    * the positive replacement rule IS present in every mode.
    """
    passage = _video("c_a", 4_000, 10_000, "the presenter defines f(x,y)")
    prompts = []
    for mode in ("qa", "explain_simple", "analogy", "summarize", "compare", "quiz"):
        system, user = build_grounded_prompt(
            "what does it mean to hold y constant", [passage], mode=mode
        )
        prompts.append((f"grounded:{mode}", system))
        prompts.append((f"grounded-user:{mode}", user))
    s_sys, s_user = build_supplemental_prompt("what is a chain rule?", [passage])
    prompts.append(("supplemental", s_sys))
    prompts.append(("supplemental-user", s_user))

    for label, text in prompts:
        lowered = text.lower()
        for phrase in RETRIEVAL_SPEAK_PHRASES:
            assert phrase not in lowered, f"{label} still contains {phrase!r}"

    for mode in ("qa", "explain_simple", "analogy", "summarize", "compare", "quiz"):
        assert "never as a search engine reading" in system_for_mode(mode).lower(), mode
    assert "never as a search engine reading" in s_sys.lower()


def test_register_requires_structure_honesty_and_latex():
    """The three register requirements the user named, present in every mode.

    LaTeX is not cosmetic: the frontend renders ``react-markdown`` +
    ``rehype-katex``, so ``$...$``/``$$...$$`` is a rendering contract, not a
    style preference. Dropping the instruction would silently break every
    formula in the product.
    """
    for mode in ("qa", "explain_simple", "analogy", "summarize", "compare", "quiz"):
        system = system_for_mode(mode)
        assert "direct answer in your first sentence" in system, mode
        assert "Define a term before you use it" in system, mode
        assert "never invent a quote, a number, or a worked example" in system, mode
        assert "$...$ inline and $$...$$ for display" in system, mode


# ---------------------------------------------------------------------------
# 7. Response contract for the frontend
# ---------------------------------------------------------------------------


@sync_test
async def test_response_fields_are_additive_and_serialised(chat_env):
    """Both new fields are always present and carry the documented values.

    The frontend agent needs certainty about the wire shape, so this asserts the
    serialised keys rather than the attributes.
    """
    covered = _video("c_a", 4_000, 10_000)
    llm = ScriptedLLM(
        LLMResult(text="Cited [chunk_id=c_a].", cited_ids=["c_a"]),
    )
    service, sid, _ = chat_env([covered], llm=llm)
    grounded = await service.chat(
        session_id=sid,
        current_time_ms=TWENTY_MIN_MS,
        message="what is a partial derivative",
        mode="qa",
    )

    llm2 = SupplementalThenGroundedLLM("**Not from the video**: general knowledge.")
    service2, sid2, _ = chat_env([covered], llm=llm2)
    supplemental = await service2.chat(
        session_id=sid2,
        current_time_ms=TWENTY_MIN_MS,
        message="what is the chain rule?",
        mode="qa",
    )

    g = grounded.model_dump()
    s = supplemental.model_dump()
    assert g["mode"] == "grounded" and g["grounded"] is True
    assert s["mode"] == "supplemental" and s["grounded"] is False
    for payload in (g, s):
        assert "mode" in payload and "grounded" in payload
        assert payload["grounded"] == (payload["mode"] == "grounded")
    # Invariant the UI can rely on without inspecting prose.
    assert supplemental.citations == []
