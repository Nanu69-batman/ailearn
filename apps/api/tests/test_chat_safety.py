"""Safety coverage for the two core product invariants (AGENTS.md Rule 6).

    Rule 6.1  strict timestamp gate -- a video passage is only ever usable
              when ``end_ms IS NOT NULL AND end_ms <= T``.
    Rule 6.2  citation validation -- an answer that cites nothing, or cites an
              id that was not in the retrieved set, is refused.

Both invariants live in code that SURVIVED the teardown (``app/chat/service.py``
and ``app/chat/prompts.py``) but whose tests did not. These tests restore them.

------------------------------------------------------------------
TEMPORARY TEST DOUBLE -- DO NOT MISTAKE FOR PRODUCT CODE
------------------------------------------------------------------
``app/retrieval/`` was deleted by the library-rebuild teardown and is a
placeholder package whose ``__getattr__`` raises ``NotImplementedError`` on
purpose (see that module's docstring and ADR-002). There is therefore no store
to inject yet: ``ChatService._retrieval()`` cannot be constructed.

To drive ``ChatService.chat()`` end to end, these tests substitute a retrieval
service at the documented ``_retrieval`` seam (``monkeypatch`` on the class
attribute -- no production file is edited) backed by ``FakeStore``, a plain
Python list of chunks. It carries no ranking, no embeddings, and no vector
library.

It deliberately does NOT gate on the timestamp. The fake returns every chunk
in scope, unfiltered by T, so ``apply_strict_timestamp`` in ``app/chat/
service.py`` is the only thing standing between a future chunk and the answer.
That is the whole point: the end-to-end tests below would FAIL if the second
gate were removed, which is what makes them worth having.

REPLACEMENT OBLIGATION: when the Chroma/Postgres-backed store lands, delete
``FakeStore``/``FakeRetrieval`` and re-point these tests at the real
``RetrievalService``. Every assertion must survive that swap unchanged. In
particular ``test_out_of_scope_resource_is_not_retrievable`` currently proves
that *the double* honours ``resource_scope``; until a real store exists the
product's own scope enforcement is UNTESTED, and the fake must not be read as
evidence that it works. See ``scope_test_status`` in the task return.
"""

from __future__ import annotations

import asyncio
import functools
from dataclasses import dataclass, field

import pytest

from app.chat.llm import LLMResult
from app.chat.prompts import REFUSAL_TEXT, extract_cited_ids
from app.chat.service import ChatService, SessionNotFoundError, apply_strict_timestamp
from app.chat.sessions import InMemoryMessageStore, InMemorySessionStore

# ``Partial-derivatives.mp4`` runs 655.5s (verified via its mvhd header), so
# these timestamps are in the middle of real course material rather than toy
# values. Kept below MAX_CURRENT_TIME_MS (6h) because ``chat()`` clamps T.
TEN_MIN_MS = 10 * 60 * 1000
TWENTY_MIN_MS = 20 * 60 * 1000
THIRTY_MIN_MS = 30 * 60 * 1000


@dataclass
class FakeChunk:
    """A retrieved passage, shaped exactly as ``app/chat`` reads one.

    Field list taken from the readers, not guessed: ``_passage_to_citation``
    and ``_passage_attrs`` in ``app/chat/service.py`` / ``app/chat/prompts.py``
    touch exactly these attributes, and ``apply_strict_timestamp`` reads
    ``source_type`` and ``end_ms``. ``score``/``resource_id`` exist because
    the real store orders by ``score`` and filters by ``resource_id``.
    """

    chunk_id: str
    resource_id: str
    source_type: str
    text: str
    start_ms: int | None = None
    end_ms: int | None = None
    video_id: str | None = None
    page_no: int | None = None
    slide_no: int | None = None
    score: float = 1.0

    # Exists so a stray ``.get``/``dict`` access in future store code does not
    # explode; the fake never reads it.
    metadata: dict = field(default_factory=dict)


class FakeStore:
    """List-backed stand-in for the retrieval store (see module docstring).

    Honours ``resource_scope`` and ``focus_resource_id`` and nothing else. In
    particular it applies NO timestamp gate, so the strict-T assertions are
    genuinely testing ``apply_strict_timestamp``.
    """

    def __init__(self, chunks: list[FakeChunk] | None = None) -> None:
        self.chunks = list(chunks or [])

    def in_scope(
        self,
        resource_scope: list[str],
        focus_resource_id: str | None = None,
    ) -> list[FakeChunk]:
        granted = set(resource_scope or [])
        rows = [c for c in self.chunks if c.resource_id in granted]
        if focus_resource_id:
            rows = [c for c in rows if c.resource_id == focus_resource_id]
        return rows


class FakeRetrieval:
    """Minimal ``RetrievalService`` replacement bound to a :class:`FakeStore`.

    Signature-compatible with the call in ``ChatService.chat``:
    ``await retrieve(query, course_id, resource_scope, current_time_ms,
    k=8, focus_resource_id=None)``.
    """

    def __init__(self, store: FakeStore, embedder=None) -> None:
        self._store = store
        self._embedder = embedder
        self.calls: list[dict] = []

    async def retrieve(
        self,
        query: str,
        course_id: str,
        resource_scope: list[str],
        current_time_ms: int,
        k: int = 8,
        focus_resource_id: str | None = None,
    ) -> list[FakeChunk]:
        self.calls.append(
            {
                "query": query,
                "course_id": course_id,
                "resource_scope": list(resource_scope or []),
                "current_time_ms": current_time_ms,
                "k": k,
                "focus_resource_id": focus_resource_id,
            }
        )
        rows = self._store.in_scope(resource_scope, focus_resource_id)
        # Stable order (newest-first like the real store) so nothing about the
        # assertions below depends on dictionary or set iteration order.
        rows.sort(key=lambda c: (-c.score, c.chunk_id))
        return rows[:k]


class ScriptedLLM:
    """Returns queued :class:`LLMResult` values and records every call.

    ``repeat_last=True`` keeps returning the final queued result, which is how
    the cite-repair tests assert "two calls, still uncited".
    """

    def __init__(self, *results: LLMResult, repeat_last: bool = False) -> None:
        self._results = list(results)
        self._repeat_last = repeat_last
        self.calls: list[dict] = []

    async def complete(self, *, system, user_prompt, passages, mode) -> LLMResult:
        self.calls.append(
            {"system": system, "user_prompt": user_prompt, "mode": mode}
        )
        idx = min(len(self.calls) - 1, len(self._results) - 1)
        if not self._results:
            return LLMResult(text=REFUSAL_TEXT, cited_ids=[])
        return self._results[idx]


def sync_test(fn):
    """Run an async test body on a private event loop.

    ``pytest-asyncio`` is not a dependency of this project (see
    ``requirements.txt``), so the async chat tests are driven with
    ``asyncio.run`` instead of a plugin marker. Deliberately dependency-free:
    adding a package for this would change the project's install surface to
    test code that needs no change.

    A fresh loop per test also means no loop-scoped state can survive between
    tests, which is part of what makes the 3x-repeat determinism check pass.
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))

    return wrapper


def _video(chunk_id: str, start_ms: int, end_ms: int | None, **kw) -> FakeChunk:
    kw.setdefault("resource_id", "res_video")
    return FakeChunk(
        chunk_id=chunk_id,
        source_type="video",
        text=f"video transcript {chunk_id}",
        start_ms=start_ms,
        end_ms=end_ms,
        video_id="vid_pderiv",
        **kw,
    )


def _pdf(chunk_id: str, page_no: int = 3, **kw) -> FakeChunk:
    kw.setdefault("resource_id", "res_pdf")
    return FakeChunk(
        chunk_id=chunk_id,
        source_type="pdf",
        text=f"pdf page {page_no} content",
        page_no=page_no,
        **kw,
    )


@pytest.fixture
def chat_env(monkeypatch):
    """Factory returning a real ``ChatService`` wired to the fake retrieval.

    Every call builds fresh session/message stores, so nothing is shared
    between tests -- that is what keeps this file order-independent (the old
    suite had a module-level store leak).

    Only ``_retrieval`` is substituted, via monkeypatch on the class
    attribute so a failure can never leave it swapped. That method is the
    documented ``TODO(library-rebuild)`` hook in ``app/chat/service.py``;
    patching it here keeps production code untouched.
    """

    def _make(chunks, llm=None, scope=None):
        sessions = InMemorySessionStore()
        messages = InMemoryMessageStore()
        store = FakeStore(chunks)
        retrieval = FakeRetrieval(store)
        provider = llm if llm is not None else ScriptedLLM()
        service = ChatService(
            store=store, embedder=None, llm=provider,
            sessions=sessions, messages=messages,
        )
        session = sessions.create(
            learner_id="learner_1",
            course_id="course_1",
            resource_id="res_video",
            lesson_id="lesson_1",
            resource_scope=list(scope or ["res_video", "res_pdf"]),
            current_time_ms=TWENTY_MIN_MS,
        )
        monkeypatch.setattr(
            ChatService, "_retrieval", lambda self: retrieval, raising=True
        )
        return service, session.id, provider, retrieval

    return _make


# ---------------------------------------------------------------------------
# Short conversational questions must not be refused at high T
# ---------------------------------------------------------------------------


def test_short_conversational_question_at_high_t_is_answered(chat_env):
    """"is this partial derivatives?" at T=10min must be answered, not refused.

    Regression for a live refusal at a late timestamp. The retrieval layer was
    innocent: the store returns 8 well-ranked passages for this exact query at
    T=600000 and the strict-T gate is satisfied by all of them. The refusal was
    produced by the LLM itself, which read the short, conversational phrasing
    as a request for information the context "does not contain" and emitted
    REFUSAL_TEXT, which ``ChatService`` passes through as a refusal.

    The fix is in the prompt (``app/chat/prompts.py``), which now tells the
    model that a topic-check question is answerable whenever a passage
    discusses the topic. Two things are asserted:

    1. The grounding system prompt carries that guidance, so the model is not
       left to infer that brevity implies missing context.
    2. With passages present, the answer is served and every citation is
       validated against the retrieved set.

    The strict-T gate is deliberately NOT touched: a refusal here was never a
    timestamp problem, and loosening the gate to "fix" it would leak future
    material. ``test_strict_modes_apply_the_gate`` and the strict-T tests above
    continue to pin that behaviour.
    """
    from app.chat.prompts import build_grounded_prompt, system_for_mode

    # (1) The prompt must state the rule the model was previously missing.
    system = system_for_mode("qa")
    assert "is this partial derivatives?" in system
    assert (
        "Only refuse when the context genuinely does not cover what was asked."
        in system
    )

    # (2) A short conversational question is answered from real passages.
    passage = _video("c_topics", start_ms=60_000, end_ms=90_000)
    llm = ScriptedLLM(
        LLMResult(
            text="Yes, this is about partial derivatives "
            "[chunk_id=c_topics].",
            cited_ids=["c_topics"],
        )
    )
    service, session_id, provider, _ = chat_env([passage], llm=llm)

    resp = asyncio.run(
        service.chat(
            session_id=session_id,
            current_time_ms=TWENTY_MIN_MS,
            message="is this partial derivatives?",
            mode="qa",
        )
    )
    assert resp.refused is False, "a short conversational question was refused"
    assert resp.answer != REFUSAL_TEXT
    assert [c.chunk_id for c in resp.citations] == ["c_topics"]

    # The prompt the model actually received carries the guidance too.
    _, user_prompt = build_grounded_prompt(
        "is this partial derivatives?", [passage], mode="qa"
    )
    assert (
        "a short or conversational question is not by "
        "itself a sign of missing information" in user_prompt
    )

    # Sanity: the cited id really was in the retrieved set (validation holds).
    assert "c_topics" in provider.calls[0]["user_prompt"]


def test_conversational_question_above_the_gate_is_still_gated(chat_env):
    """The prompt fix must not become a way around the strict-T gate.

    Same short conversational phrasing, but the only passage ends AFTER the
    learner's timestamp. It is not eligible, so the answer must be refused --
    this is the leak the prompt change could have opened and must not.
    """
    future = _video("c_future", start_ms=700_000, end_ms=800_000)
    service, session_id, _, _ = chat_env([future], scope=["res_video"])

    resp = asyncio.run(
        service.chat(
            session_id=session_id,
            current_time_ms=TWENTY_MIN_MS,
            message="is this partial derivatives?",
            mode="qa",
        )
    )
    assert resp.refused is True
    assert resp.citations == []


# ---------------------------------------------------------------------------
# Rule 6.1 -- strict timestamp gate
# ---------------------------------------------------------------------------


def test_video_chunk_past_t_is_excluded():
    """A video passage ending after T is dropped, not returned."""
    t = TWENTY_MIN_MS
    past = _video("c_past", 60_000, TEN_MIN_MS)
    future = _video("c_future", TWENTY_MIN_MS + 1_000, THIRTY_MIN_MS)

    kept = apply_strict_timestamp([past, future], t, "summarize")

    assert [p.chunk_id for p in kept] == ["c_past"]


def test_video_chunk_within_t_is_included():
    """A video passage ending exactly at T is kept (inclusive boundary)."""
    t = TWENTY_MIN_MS
    at_boundary = _video("c_at_t", TEN_MIN_MS, t)
    earlier = _video("c_earlier", 60_000, TEN_MIN_MS)

    kept = apply_strict_timestamp([at_boundary, earlier], t, "summarize")

    assert [p.chunk_id for p in kept] == ["c_at_t", "c_earlier"]


def test_video_chunk_with_null_end_ms_is_excluded():
    """NULL ``end_ms`` is EXCLUDE, not UNKNOWN (ADR-002 section 5.1).

    Dropping the ``IS NOT NULL`` check is a disguised behaviour change: it
    silently admits a chunk with no known end, which for a transcript window
    can be anywhere on the timeline. This is the regression that must not
    come back, so it is asserted directly.
    """
    t = TWENTY_MIN_MS
    null_end = _video("c_null_end", 60_000, None)
    # start_ms is far in the past, so a start_ms-based gate would keep it.
    assert null_end.start_ms < t

    kept = apply_strict_timestamp([null_end], t, "summarize")

    assert kept == [], "a video chunk with NULL end_ms must be excluded"


def test_gate_uses_end_ms_never_start_ms():
    """THE critical test. A chunk spanning ``start_ms < T < end_ms`` is dropped.

    This chunk *looks* covered -- it starts before the learner reached T. But
    its tail runs past T, so quoting it leaks material the learner has not
    reached. A gate relaxed to ``start_ms`` measured a 48% spoiler rate
    (AGENTS.md Rule 6.1, ADR-002 section 5.1).
    """
    t = TWENTY_MIN_MS
    spanning = _video(
        "c_spanning", TWENTY_MIN_MS - 5_000, TWENTY_MIN_MS + 5_000
    )

    assert spanning.start_ms < t < spanning.end_ms
    kept = apply_strict_timestamp([spanning], t, "summarize")

    assert kept == [], "a chunk spanning T must be excluded (end_ms gate only)"


def test_docs_are_not_time_gated():
    """PDF/pptx passages survive regardless of T -- T never applies to docs."""
    t = TEN_MIN_MS
    pdf = _pdf("c_pdf", page_no=7)
    # A doc chunk with no temporal bounds at all, the realistic shape.
    assert pdf.start_ms is None and pdf.end_ms is None

    kept = apply_strict_timestamp([pdf], t, "summarize")

    assert [p.chunk_id for p in kept] == ["c_pdf"]


@pytest.mark.parametrize("mode", ["summarize", "quiz"])
def test_strict_modes_apply_the_gate(mode):
    """Both strict modes gate; non-strict modes pass the future chunk through."""
    t = TWENTY_MIN_MS
    future = _video("c_future", TWENTY_MIN_MS + 1_000, THIRTY_MIN_MS)

    gated = apply_strict_timestamp([future], t, mode)
    passthrough = apply_strict_timestamp([future], t, "qa")

    assert gated == [], f"mode={mode} must apply the strict-T gate"
    assert [p.chunk_id for p in passthrough] == ["c_future"]


@sync_test
async def test_video_chunk_past_t_is_excluded_end_to_end(chat_env):
    """End to end: a future chunk never reaches the answer or the citations.

    ``FakeRetrieval`` returns this chunk ungated, so the only thing that can
    remove it is ``apply_strict_timestamp`` inside ``ChatService.chat``.

    Two variants, because the two gates compose and the composition matters:

    (a) the model *tries* to cite the dropped chunk -> the strict-T gate
        removes it, so the id is unknown to the citation guard, and the whole
        answer is refused. Two independent layers fire; nothing partial is
        served.
    (b) the model cites only legitimate chunks -> the answer IS served, and
        the future chunk is simply absent. This is the variant that would
        fail if the gate were relaxed to ``start_ms``.
    """
    leaking = ScriptedLLM(
        LLMResult(
            text="Summary of covered material [chunk_id=c_future, chunk_id=c_doc]",
            cited_ids=["c_future", "c_doc"],
        )
    )
    service, sid, _, _ = chat_env(
        [_video("c_future", TWENTY_MIN_MS + 1_000, THIRTY_MIN_MS), _pdf("c_doc")],
        llm=leaking,
    )

    resp = await service.chat(
        session_id=sid,
        current_time_ms=TWENTY_MIN_MS,
        message="summarize what we covered",
        mode="summarize",
    )

    assert resp.refused is True
    assert resp.citations == []
    assert "c_future" not in resp.answer

    clean = ScriptedLLM(
        LLMResult(text="Summary of covered material [chunk_id=c_doc]",
                  cited_ids=["c_doc"])
    )
    service2, sid2, _, _ = chat_env(
        [_video("c_future", TWENTY_MIN_MS + 1_000, THIRTY_MIN_MS), _pdf("c_doc")],
        llm=clean,
    )

    resp2 = await service2.chat(
        session_id=sid2,
        current_time_ms=TWENTY_MIN_MS,
        message="summarize what we covered",
        mode="summarize",
    )

    assert not resp2.refused
    assert [c.chunk_id for c in resp2.citations] == ["c_doc"]
    assert "c_future" not in resp2.answer


@sync_test
async def test_gate_uses_end_ms_never_start_ms_end_to_end(chat_env):
    """The 48%-spoiler test, end to end: a spanning chunk must not be cited.

    ``FakeRetrieval`` hands back the spanning chunk ungated; it starts before
    T and ends after T. If the gate were relaxed to ``start_ms``, it would
    survive, validate against itself, and appear in ``citations`` -- exactly
    the spoiler leak this file exists to prevent.
    """
    spanning = _video("c_spanning", TWENTY_MIN_MS - 5_000, TWENTY_MIN_MS + 5_000)
    chunks = [spanning, _pdf("c_doc")]

    # (a) model cites the spanning chunk -> strict-T drops it, the id becomes
    #     unknown, and the answer is refused rather than partially served.
    leaking = ScriptedLLM(
        LLMResult(
            text="Covered: [chunk_id=c_spanning, chunk_id=c_doc]",
            cited_ids=["c_spanning", "c_doc"],
        )
    )
    service, sid, _, _ = chat_env(chunks, llm=leaking)

    resp = await service.chat(
        session_id=sid,
        current_time_ms=TWENTY_MIN_MS,
        message="summarize what we covered",
        mode="summarize",
    )

    assert resp.refused is True
    assert resp.citations == []

    # (b) model cites only legitimate chunks -> served, spanning chunk absent.
    clean = ScriptedLLM(
        LLMResult(text="Covered: [chunk_id=c_doc]", cited_ids=["c_doc"])
    )
    service2, sid2, _, _ = chat_env(chunks, llm=clean)

    resp2 = await service2.chat(
        session_id=sid2,
        current_time_ms=TWENTY_MIN_MS,
        message="summarize what we covered",
        mode="summarize",
    )

    assert not resp2.refused
    cited = [c.chunk_id for c in resp2.citations]
    assert cited == ["c_doc"], f"spanning chunk leaked end to end: {cited}"
    assert "c_spanning" not in resp2.answer


@sync_test
async def test_refusal_when_no_passages(chat_env):
    """No passages at all -> refusal, even in a strict mode."""
    service, sid, llm, _ = chat_env([], llm=ScriptedLLM())

    resp = await service.chat(
        session_id=sid,
        current_time_ms=TWENTY_MIN_MS,
        message="what is a partial derivative?",
        mode="summarize",
    )

    assert resp.refused is True
    assert resp.answer == REFUSAL_TEXT
    assert resp.citations == []
    assert llm.calls == [], "no LLM call should happen with no passages"


# ---------------------------------------------------------------------------
# Rule 6.2 -- citation validation
# ---------------------------------------------------------------------------


@sync_test
async def test_unknown_citation_id_is_refused(chat_env):
    """An answer citing an id absent from the retrieved set is refused."""
    llm = ScriptedLLM(
        LLMResult(
            text="Partial derivatives measure change along one axis "
            "[chunk_id=c_real, chunk_id=c_invented]",
            cited_ids=["c_real", "c_invented"],
        )
    )
    service, sid, _, _ = chat_env([_video("c_real", 0, TEN_MIN_MS)], llm=llm)

    resp = await service.chat(
        session_id=sid,
        current_time_ms=TWENTY_MIN_MS,
        message="what is a partial derivative?",
        mode="qa",
    )

    assert resp.refused is True
    assert resp.answer == REFUSAL_TEXT
    assert resp.citations == []


@sync_test
async def test_uncited_answer_is_refused(chat_env):
    """A substantive answer with zero citations is ungrounded -> refused."""
    llm = ScriptedLLM(
        LLMResult(
            text="A partial derivative measures the rate of change along one axis.",
            cited_ids=[],
        ),
        repeat_last=True,
    )
    service, sid, _, _ = chat_env([_video("c_real", 0, TEN_MIN_MS)], llm=llm)

    resp = await service.chat(
        session_id=sid,
        current_time_ms=TWENTY_MIN_MS,
        message="what is a partial derivative?",
        mode="qa",
    )

    assert resp.refused is True
    assert resp.answer == REFUSAL_TEXT
    assert resp.citations == []


@sync_test
async def test_uncited_answer_gets_one_repair_retry(chat_env):
    """Exactly one repair retry, then refusal if it still cites nothing.

    Asserting the call count is the point: zero retries means ungrounded
    answers leak, unbounded retries means a hang. Both directions are bugs.
    """
    llm = ScriptedLLM(
        LLMResult(text="An uncited substantive answer.", cited_ids=[]),
        LLMResult(text="Still uncited after repair.", cited_ids=[]),
    )
    service, sid, _, _ = chat_env([_video("c_real", 0, TEN_MIN_MS)], llm=llm)

    resp = await service.chat(
        session_id=sid,
        current_time_ms=TWENTY_MIN_MS,
        message="what is a partial derivative?",
        mode="qa",
    )

    assert resp.refused is True
    assert resp.citations == []
    assert len(llm.calls) == 2, "expected exactly one cite-repair retry"
    # The repair prompt must name the failure and carry the prior answer,
    # otherwise the model has nothing to fix.
    repair_prompt = llm.calls[1]["user_prompt"]
    assert "Previous uncited answer" in repair_prompt
    assert "zero citations" in repair_prompt


@sync_test
async def test_uncited_answer_repaired_with_valid_citation_is_accepted(chat_env):
    """The retry is allowed to succeed: a cited second answer is served."""
    llm = ScriptedLLM(
        LLMResult(text="An uncited substantive answer.", cited_ids=[]),
        LLMResult(
            text="A partial derivative measures change [chunk_id=c_real]",
            cited_ids=["c_real"],
        ),
    )
    service, sid, _, _ = chat_env([_video("c_real", 0, TEN_MIN_MS)], llm=llm)

    resp = await service.chat(
        session_id=sid,
        current_time_ms=TWENTY_MIN_MS,
        message="what is a partial derivative?",
        mode="qa",
    )

    assert resp.refused is False
    assert [c.chunk_id for c in resp.citations] == ["c_real"]
    assert len(llm.calls) == 2


@sync_test
async def test_citation_markers_are_stripped_from_answer(chat_env):
    """Transport markers never reach the user, but ``citations[]`` stays full.

    Covers both marker shapes Gemini emits: a single ``[chunk_id=abc]`` and a
    bunched ``[chunk_id=a, chunk_id=b]``. The UI renders badges from
    ``citations[]``, so a leaked marker is a visible bug.
    """
    single = ScriptedLLM(
        LLMResult(
            text="A partial derivative measures change [chunk_id=c_real].",
            cited_ids=["c_real"],
        )
    )
    service, sid, _, _ = chat_env([_video("c_real", 0, TEN_MIN_MS)], llm=single)

    resp = await service.chat(
        session_id=sid,
        current_time_ms=TWENTY_MIN_MS,
        message="what is a partial derivative?",
        mode="qa",
    )

    assert not resp.refused
    assert "chunk_id" not in resp.answer
    assert "[chunk_id=" not in resp.answer
    assert [c.chunk_id for c in resp.citations] == ["c_real"]
    assert "partial derivative measures change" in resp.answer

    bunched = ScriptedLLM(
        LLMResult(
            text="Both facts hold [chunk_id=c_real, chunk_id=c_doc].",
            cited_ids=["c_real", "c_doc"],
        )
    )
    service2, sid2, _, _ = chat_env(
        [_video("c_real", 0, TEN_MIN_MS), _pdf("c_doc")], llm=bunched
    )

    resp2 = await service2.chat(
        session_id=sid2,
        current_time_ms=TWENTY_MIN_MS,
        message="compare video and pdf",
        mode="qa",
    )

    assert not resp2.refused
    assert "chunk_id" not in resp2.answer
    assert "[" not in resp2.answer
    assert sorted(c.chunk_id for c in resp2.citations) == ["c_doc", "c_real"]


@sync_test
async def test_bunched_marker_ids_are_all_validated(chat_env):
    """Regression guard: every ID inside a bunched marker gets validated.

    The old ``CITATION_RE`` character class excluded ``=``, so Gemini's
    ``[chunk_id=aaa, chunk_id=bbb]`` never matched. The marker was neither
    stripped from the answer nor extracted for validation, so a
    video-derived answer citing an invented id reached the user with only an
    unrelated trailing PDF citation. Here the *second* ID is invalid: if
    bunched IDs escape extraction, this test fails.
    """
    llm = ScriptedLLM(
        LLMResult(
            text="Answer citing one real and one invented id "
            "[chunk_id=c_real, chunk_id=c_invented].",
            cited_ids=[],
        )
    )
    service, sid, _, _ = chat_env([_video("c_real", 0, TEN_MIN_MS)], llm=llm)

    resp = await service.chat(
        session_id=sid,
        current_time_ms=TWENTY_MIN_MS,
        message="what is a partial derivative?",
        mode="qa",
    )

    assert resp.refused is True, "an invalid id inside a bunched marker escaped"
    assert resp.citations == []


def test_extract_cited_ids_reads_every_bunched_shape():
    """Unit-level guard on the extractor the whole citation flow depends on."""
    assert extract_cited_ids("x [chunk_id=abc]") == ["abc"]
    assert extract_cited_ids("x [chunk_id=aaa, chunk_id=bbb]") == ["aaa", "bbb"]
    assert extract_cited_ids("x [chunk_id=aaa, bbb]") == ["aaa", "bbb"]
    # Ordinary bracketed prose must not be harvested for citations.
    assert extract_cited_ids("see the table [Table 3] for details") == []


# ---------------------------------------------------------------------------
# Scope enforcement
# ---------------------------------------------------------------------------


@sync_test
async def test_out_of_scope_resource_is_not_retrievable(chat_env):
    """A chunk from an ungranted resource must not be returned or cited.

    HONEST SCOPE NOTE: in the surviving code, scope is handed to retrieval as
    ``session.resource_scope`` and enforcement happens inside the store --
    which was deleted. ``ChatService.chat`` does not re-filter. So this test
    exercises ``FakeStore``, not product code: it pins the contract the new
    store MUST honour (filter on ``resource_scope``, honour
    ``focus_resource_id``) and it will keep that contract honest once the real
    store is swapped in, but it is NOT currently evidence that the product
    enforces scope. The product-level assertion to add the moment a store
    exists is that a chunk from an ungranted ``resource_id`` is absent from
    ``citations`` even when the model names it.
    """
    granted = _video("c_granted", 0, TEN_MIN_MS)
    hidden = _video(
        "c_hidden", 0, TEN_MIN_MS, resource_id="res_other_video"
    )
    llm = ScriptedLLM(
        LLMResult(
            text="Answer citing both [chunk_id=c_granted, chunk_id=c_hidden].",
            cited_ids=["c_granted", "c_hidden"],
        )
    )
    service, sid, _, retrieval = chat_env(
        [granted, hidden], llm=llm, scope=["res_video", "res_pdf"]
    )

    resp = await service.chat(
        session_id=sid,
        current_time_ms=TWENTY_MIN_MS,
        message="what is covered?",
        mode="qa",
    )

    # The fake store honoured the granted scope...
    assert retrieval.calls[0]["resource_scope"] == ["res_video", "res_pdf"]
    assert "c_hidden" not in [c.chunk_id for c in
                             FakeStore([granted, hidden]).in_scope(
                                 ["res_video", "res_pdf"])]
    # ...so the hidden chunk cannot be cited. chat() has no filter of its own;
    # the unknown-id guard is what currently stops the smuggled citation.
    assert resp.refused is True
    assert resp.citations == []


@sync_test
async def test_chat_passes_granted_scope_and_t_to_retrieval(chat_env):
    """The hand-off itself: scope and clamped T must reach retrieval intact."""
    service, sid, _, retrieval = chat_env(
        [_video("c_real", 0, TEN_MIN_MS)], scope=["res_video"]
    )

    await service.chat(
        session_id=sid,
        current_time_ms=TWENTY_MIN_MS,
        message="what is a partial derivative?",
        mode="qa",
    )

    call = retrieval.calls[0]
    assert call["resource_scope"] == ["res_video"]
    assert call["course_id"] == "course_1"
    assert call["current_time_ms"] == TWENTY_MIN_MS
    assert call["k"] == 8


@sync_test
async def test_unknown_session_raises(chat_env):
    """A session id that was never issued must not be answered."""
    service, _, _, _ = chat_env([_pdf("c_doc")])

    with pytest.raises(SessionNotFoundError):
        await service.chat(
            session_id="ses_does_not_exist",
            current_time_ms=TWENTY_MIN_MS,
            message="what is covered?",
            mode="qa",
        )


@sync_test
async def test_t_is_clamped_to_the_server_ceiling(chat_env):
    """A client claiming T beyond 6h is clamped, not honoured verbatim.

    Without the clamp, T=end_of_video would unlock the whole lecture through
    the summarize/quiz gate.
    """
    from app.chat.schemas import MAX_CURRENT_TIME_MS

    future = _video("c_future", TWENTY_MIN_MS + 1_000, THIRTY_MIN_MS)
    llm = ScriptedLLM(
        LLMResult(text="Summary [chunk_id=c_future]", cited_ids=["c_future"])
    )
    service, sid, _, retrieval = chat_env([future], llm=llm)

    resp = await service.chat(
        session_id=sid,
        current_time_ms=MAX_CURRENT_TIME_MS + 10_000_000,
        message="summarize",
        mode="summarize",
    )

    assert retrieval.calls[0]["current_time_ms"] == MAX_CURRENT_TIME_MS
    # Clamped to 6h, which is still past this chunk's end, so it is served.
    assert not resp.refused
    assert [c.chunk_id for c in resp.citations] == ["c_future"]
# ---------------------------------------------------------------------------
# Empty scope (grant does not cover the focused resource) vs low evidence
# ---------------------------------------------------------------------------


@sync_test
async def test_empty_scope_is_distinguishable_from_low_evidence(chat_env):
    """A session granted only a deleted resource must not blame the learner.

    Reproduces the live failure: the session grant holds a resource that no
    longer exists, the learner opens a different one, and every question
    returned the low-evidence message -- an explanation about watching that was
    never true. The response must now carry ``scope_empty`` and an honest
    message, and must NOT contain the low-evidence text.
    """
    from app.chat.prompts import SCOPE_REFUSAL_TEXT

    granted = _video("c_granted", 0, TEN_MIN_MS)          # res_video, in grant
    viewing = _video("c_viewing", 0, TEN_MIN_MS, resource_id="res_49ccbdb02150")
    service, sid, provider, retrieval = chat_env(
        [granted, viewing], scope=["res_2a03ae0ee38e"]
    )

    resp = await service.chat(
        session_id=sid,
        current_time_ms=TWENTY_MIN_MS,
        message="What equation is on screen?",
        mode="qa",
        focus_resource_id="res_49ccbdb02150",
    )

    assert resp.scope_empty is True, "empty scope must be flagged for the client"
    assert resp.refused is True
    assert resp.answer == SCOPE_REFUSAL_TEXT
    # The distinguishing property: NOT the "not watched enough" story.
    assert "enough information" not in resp.answer
    assert resp.answer != REFUSAL_TEXT
    assert resp.citations == []
    # Fail closed before retrieval -- nothing was searched at all.
    assert retrieval.calls == []
    assert provider.calls == []
    # No internal ids or implementation detail in the learner-facing text.
    assert "res_2a03ae0ee38e" not in resp.answer
    assert "resource_scope" not in resp.answer


@sync_test
async def test_low_evidence_refusal_unchanged(chat_env):
    """A genuine in-scope-but-before-T question refuses exactly as before.

    Guards requirement 5: the strict-T gate and the ordinary low-evidence
    message are untouched, and no scope signal leaks into that path.
    """
    from app.chat.prompts import SCOPE_REFUSAL_TEXT

    future = _video("c_future", TWENTY_MIN_MS + 1_000, THIRTY_MIN_MS)
    service, sid, _, _ = chat_env([future], scope=["res_video", "res_pdf"])

    resp = await service.chat(
        session_id=sid,
        current_time_ms=TWENTY_MIN_MS,
        message="what is covered?",
        mode="qa",
        focus_resource_id="res_video",
    )

    assert resp.refused is True
    assert resp.answer == REFUSAL_TEXT, "the low-evidence message must not change"
    assert resp.scope_empty is False, "an in-scope question is not a scope failure"
    assert resp.answer != SCOPE_REFUSAL_TEXT
    assert resp.citations == []


@sync_test
async def test_scope_signal_requires_empty_intersection(chat_env):
    """Non-empty intersections never emit the scope signal, answered or not."""
    from app.chat.prompts import SCOPE_REFUSAL_TEXT

    granted = _video("c_granted", 0, TEN_MIN_MS)          # res_video, in grant
    llm = ScriptedLLM(
        LLMResult(
            text="It shows f'(x) [chunk_id=c_granted].", cited_ids=["c_granted"]
        )
    )
    service, sid, _, _ = chat_env([granted], llm=llm, scope=["res_video", "res_pdf"])

    # (1) focus inside the grant -> answered, no signal
    ok = await service.chat(
        session_id=sid,
        current_time_ms=TWENTY_MIN_MS,
        message="what is on screen?",
        mode="qa",
        focus_resource_id="res_video",
    )
    assert ok.refused is False
    assert ok.scope_empty is False
    assert ok.answer != SCOPE_REFUSAL_TEXT
    assert [c.chunk_id for c in ok.citations] == ["c_granted"]

    # (2) no focus at all -> still the whole grant, no signal
    ok2 = await service.chat(
        session_id=sid,
        current_time_ms=TWENTY_MIN_MS,
        message="what is on screen?",
        mode="qa",
    )
    assert ok2.refused is False
    assert ok2.scope_empty is False


@sync_test
async def test_scope_grant_is_never_widened(chat_env):
    """Regression guard: focus OUTSIDE the grant does not re-open the grant.

    The original bug replaced the grant with ``focus_resource_id``. Here the
    grant holds ``res_video`` and the learner focuses ``res_other_video``: the
    intersection is empty, so the in-grant chunk must NOT be searched or cited.
    """
    granted = _video("c_granted", 0, TEN_MIN_MS)                     # res_video
    ungranted = _video(
        "c_ungranted", 0, TEN_MIN_MS, resource_id="res_other_video"
    )
    llm = ScriptedLLM(
        LLMResult(
            text="Both here [chunk_id=c_granted, chunk_id=c_ungranted].",
            cited_ids=["c_granted", "c_ungranted"],
        )
    )
    service, sid, provider, retrieval = chat_env(
        [granted, ungranted], llm=llm, scope=["res_video"]
    )

    resp = await service.chat(
        session_id=sid,
        current_time_ms=TWENTY_MIN_MS,
        message="what is on screen?",
        mode="qa",
        focus_resource_id="res_other_video",
    )

    assert resp.scope_empty is True
    assert resp.refused is True
    assert resp.citations == []
    # Nothing was searched, so neither chunk could be read.
    assert retrieval.calls == []
    assert provider.calls == []
    # Independent proof the grant was not swapped for the focused id.
    from app.chat.service import effective_resource_scope

    assert effective_resource_scope(["res_video"], "res_other_video") == []
    assert effective_resource_scope(["res_video"], None) == ["res_video"]
    assert effective_resource_scope(["res_video"], "res_video") == ["res_video"]
