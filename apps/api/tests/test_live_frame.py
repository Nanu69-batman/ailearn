"""Live-frame capture: answer about the frame actually on the learner's screen.

The feature, in one sentence: **the live frame is a passage at exactly T**
(``start_ms == end_ms == T``), which is why it needs no special-casing in the
strict-T gate -- ``end_ms <= T`` holds with equality.

What these tests exist to hold:

    * the passage really does sit at T, and really is admitted by the gate;
    * no returned citation ever exceeds T, at any T;
    * an out-of-range T is REJECTED, never silently clamped onto a neighbouring
      moment (clamping is the exact substitution
      ``app.ingestion.visual._validated_descriptions`` refuses to make);
    * the frame classifier fires on frame questions and only on those, and the
      explicit button overrides the wording;
    * the live frame is injected ALONGSIDE retrieved strict-T passages, never
      instead of them;
    * a document resource degrades honestly -- no frame is fabricated;
    * re-asking at the same second costs no extra vision call.

Every test here is offline. The vision call is monkeypatched, the cache is
redirected at ``tmp_path``, and the HTTP fixture mounts only the chat router on
a bare app -- so a run cannot write into ``apps/api/data/``, cannot open the real
Chroma index, and cannot reach the live API.
"""

from __future__ import annotations

import asyncio
import functools
from pathlib import Path

import pytest

from app.chat.llm import LLMResult
from app.chat.prompts import MARKER_RE, build_grounded_prompt, extract_cited_ids
from app.chat.service import ChatService
from app.chat.sessions import InMemoryMessageStore, InMemorySessionStore
from app.ingestion import liveframe as liveframe_mod
from app.ingestion.liveframe import (
    LiveFrame,
    LiveFrameRejected,
    LiveFrameUnavailable,
    assert_live_frame_at,
    describe_live_frame,
    frame_cache_key,
    live_chunk_id,
    live_frame_passage,
    resource_has_frame,
    validate_frame_time_ms,
)
from app.retrieval.recency import is_frame_question, is_recency_seeking
from app.retrieval.store import (
    NO_END_MS,
    TIME_GATED_SOURCE_TYPES,
    ChromaVectorStore,
    build_where,
)

#: `Partial-derivatives.mp4` is 655.5s; use real-course timestamps, not toys.
DURATION_MS = 655_500
RESOURCE = "res_2e629d682a3d"
COURSE = "demo"

JPEG = b"\xff\xd8\xff\xe0 fake jpeg bytes \xff\xd9"

#: Stand-in for the vector store in tests whose retrieval seam is stubbed.
_UNUSED_STORE = object()


def sync_test(fn):
    """Run an async body on a private loop (no pytest-asyncio in this project)."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))

    return wrapper


def make_frame(at_ms: int, text: str = "a slide titled Partial Derivatives") -> LiveFrame:
    return LiveFrame(
        resource_id=RESOURCE, course_id=COURSE, at_ms=at_ms, text=text
    )


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class CountingVision:
    """Stand-in for ``app.ingestion.visual._gemini_describe``.

    Counts calls, because "re-asking costs no extra vision call" is only
    testable against something whose calls can be counted.
    """

    def __init__(self, description: str = "a slide titled Partial Derivatives"):
        self.description = description
        self.calls: list[list[tuple[int, bytes]]] = []

    def __call__(self, frames):
        self.calls.append(list(frames))
        return {0: self.description}


class FakePassage:
    """A retrieved passage shaped exactly as ``app.chat`` reads one."""

    def __init__(self, chunk_id, *, source_type="video", kind="transcript",
                 start_ms=0, end_ms=60_000, resource_id=RESOURCE, text="audio"):
        self.chunk_id = chunk_id
        self.resource_id = resource_id
        self.course_id = COURSE
        self.source_type = source_type
        self.text = text
        self.kind = kind
        self.start_ms = start_ms
        self.end_ms = end_ms
        self.video_id = "vid_1"
        self.page_no = 3 if source_type == "pdf" else None
        self.slide_no = None
        self.image_no = None
        self.score = 1.0
        self.metadata = {"kind": kind or ""}


class FakeRetrieval:
    """Records the call and returns a fixed list. Applies no gate itself."""

    def __init__(self, passages):
        self._passages = list(passages)
        self.calls: list[dict] = []

    async def retrieve(self, query, course_id, resource_scope, current_time_ms,
                       k=8, focus_resource_id=None, prefer_recent=False):
        self.calls.append({
            "query": query,
            "current_time_ms": current_time_ms,
            "resource_scope": list(resource_scope or []),
            "prefer_recent": prefer_recent,
            "k": k,
        })
        return list(self._passages)


class EchoLLM:
    """Cites only the first passage -- the live frame when one is present."""

    def __init__(self, text: str | None = None):
        self._text = text
        self.calls: list[list] = []
        self.prompt = ""

    async def complete(self, *, system, user_prompt, passages, mode):
        self.calls.append(list(passages))
        self.prompt = user_prompt
        first = passages[0]
        body = self._text if self._text is not None else first.text
        return LLMResult(
            text=f"{body} [chunk_id={first.chunk_id}]",
            cited_ids=[first.chunk_id],
        )


class CiteAllLLM:
    """Cites every passage, so we can prove the retrieved audio survives."""

    def __init__(self):
        self.calls: list[list] = []
        self.prompt = ""

    async def complete(self, *, system, user_prompt, passages, mode):
        self.calls.append(list(passages))
        self.prompt = user_prompt
        ids = [p.chunk_id for p in passages]
        marks = " ".join(f"[chunk_id={i}]" for i in ids)
        return LLMResult(text=f"both kinds of evidence. {marks}", cited_ids=ids)


@pytest.fixture
def chat_env(monkeypatch):
    """Factory: a real ChatService with only the retrieval seam substituted."""

    def _make(passages, llm=None):
        sessions = InMemorySessionStore()
        messages = InMemoryMessageStore()
        retrieval = FakeRetrieval(passages)
        provider = llm if llm is not None else EchoLLM()
        service = ChatService(
            # A sentinel, NOT None: `store=None` would resolve the process-wide
            # shared store and open the developer's real Chroma index under
            # data/. The retrieval seam is stubbed, so this is never called.
            store=_UNUSED_STORE, embedder=None, llm=provider,
            sessions=sessions, messages=messages,
        )
        session = sessions.create(
            learner_id="learner_1", course_id=COURSE, resource_id=RESOURCE,
            lesson_id="lesson_1", resource_scope=[RESOURCE],
            current_time_ms=0,
        )
        monkeypatch.setattr(
            ChatService, "_retrieval", lambda self: retrieval, raising=True
        )
        return service, session.id, provider, retrieval

    return _make


# ---------------------------------------------------------------------------
# 1. The passage sits at exactly T, so the gate admits it with NO special case
# ---------------------------------------------------------------------------


def test_live_frame_passage_sits_exactly_at_t():
    """start_ms == end_ms == T -- the whole design, in one assertion set.

    ``end_ms <= T`` must hold with EQUALITY, and ``start_ms == end_ms`` is what
    makes the frame an instant observation rather than a span. If this test ever
    needs a tolerance or a relaxed comparison to pass, the design has broken.
    """
    at_ms = 300_000
    passage = live_frame_passage(make_frame(at_ms))

    assert passage.start_ms == at_ms
    assert passage.end_ms == at_ms
    # The gate's own predicate, re-evaluated rather than assumed.
    assert passage.end_ms <= at_ms
    assert passage.source_type in TIME_GATED_SOURCE_TYPES
    assert passage.kind == "visual"
    assert passage.metadata["live"] is True
    assert ChromaVectorStore._passes_strict_t(passage, at_ms) is True
    assert_live_frame_at(passage, at_ms)

    # `image` stays ungated, and the unknown-end sentinel is untouched: the
    # feature adds no arm to TIME_GATED_SOURCE_TYPES.
    assert "image" not in TIME_GATED_SOURCE_TYPES
    assert TIME_GATED_SOURCE_TYPES == frozenset(
        {"video", "video_transcript", "visual", "visual_frame"}
    )
    assert NO_END_MS == -1

    # The `where` clause admits it unchanged, and never compares start_ms.
    where = build_where(
        course_id=COURSE, resource_scope=[RESOURCE], current_time_ms=at_ms
    )
    rendered = repr(where)
    assert "start_ms" not in rendered, "the gate must never compare start_ms"
    assert str(at_ms) in rendered

    # The id is citable: it must survive the citation-marker parser, or the
    # model could describe this frame but never point at it.
    cid = live_chunk_id(RESOURCE, at_ms)
    assert cid == f"live_{RESOURCE}_{at_ms}"
    assert extract_cited_ids(f"x [chunk_id={cid}]") == [cid]
    assert MARKER_RE.search(f"[chunk_id={cid}]") is not None


# ---------------------------------------------------------------------------
# 2. No returned citation ever exceeds T, for any T
# ---------------------------------------------------------------------------


@sync_test
async def test_live_frame_never_exceeds_t(chat_env):
    """Across several T, every citation in the answer satisfies ``end_ms <= T``.

    Includes T=2000 (barely into the video) and T past the sampled-frame horizon,
    because a leak is most likely exactly where retrieval is starved.
    """
    for t_ms in (0, 2000, 30_000, 300_000, 600_000):
        audio = FakePassage("c_audio", start_ms=0, end_ms=min(60_000, t_ms))
        service, session_id, _provider, _retrieval = chat_env([audio])
        resp = await service.chat(
            session_id=session_id,
            current_time_ms=t_ms,
            message="what am I looking at?",
            mode="qa",
            live_frame=make_frame(t_ms),
        )
        assert resp.refused is False, f"refused at T={t_ms}"
        assert resp.live_frame == "live"
        assert resp.citations, f"no citation at T={t_ms}"
        for c in resp.citations:
            assert c.end_ms is not None, f"NULL end_ms at T={t_ms}"
            assert c.end_ms <= t_ms, (
                f"strict-T leak: citation ends at {c.end_ms} > T={t_ms}"
            )
            if c.live:
                # A live citation is an instant AT T, never a span around it.
                assert c.start_ms == c.end_ms == t_ms


# ---------------------------------------------------------------------------
# 3. An out-of-range T is rejected -- never clamped
# ---------------------------------------------------------------------------


def test_out_of_range_frame_is_rejected():
    """T < 0, T > duration, and a client-asserted duration are all refused.

    The third case is the important one: the client is never asked for a duration
    at all -- ``validate_frame_time_ms`` has no such parameter -- so a client
    claiming the video is twelve hours long cannot widen the window. The bound
    comes from the server's own container header.
    """
    assert validate_frame_time_ms(at_ms=0, duration_ms=DURATION_MS) == 0
    assert validate_frame_time_ms(at_ms=DURATION_MS, duration_ms=DURATION_MS) == DURATION_MS
    assert validate_frame_time_ms(at_ms=30_000, duration_ms=DURATION_MS) == 30_000

    # Before the start.
    with pytest.raises(LiveFrameRejected) as neg:
        validate_frame_time_ms(at_ms=-1, duration_ms=DURATION_MS)
    assert "before the start" in str(neg.value)
    with pytest.raises(LiveFrameRejected):
        validate_frame_time_ms(at_ms=-30_000, duration_ms=DURATION_MS)

    # Past the end -- the live-probe case (999999999).
    with pytest.raises(LiveFrameRejected) as over:
        validate_frame_time_ms(at_ms=999_999_999, duration_ms=DURATION_MS)
    assert "past the end" in str(over.value)
    assert str(DURATION_MS) in str(over.value)

    # Not a number.
    with pytest.raises(LiveFrameRejected):
        validate_frame_time_ms(at_ms="not-a-time", duration_ms=DURATION_MS)

    # There is no parameter through which a client duration could arrive.
    import inspect

    assert set(inspect.signature(validate_frame_time_ms).parameters) == {
        "at_ms",
        "duration_ms",
    }

    # An unknown server-side duration is a hard stop, not a licence to accept.
    with pytest.raises(LiveFrameUnavailable):
        validate_frame_time_ms(at_ms=30_000, duration_ms=None)

    # Every out-of-range value raises and yields nothing a caller could mistake
    # for a clamped T: there is no return path at all.
    for bad in (-1, -30_000, DURATION_MS + 1, 999_999_999, 2**40):
        with pytest.raises(LiveFrameRejected):
            validate_frame_time_ms(at_ms=bad, duration_ms=DURATION_MS)


# ---------------------------------------------------------------------------
# 4. Classifier, and the button override
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "what am I looking at",
        "what am I looking at right now?",
        "describe this frame",
        "what does this say on screen",
        "read the screen",
        "what is on the screen right now?",
        "what equation is on screen",
        "explain this slide",
        "this frame",
        "what is shown on this frame",
        "what text is on the screen",
        "what does the slide say",
    ],
)
def test_frame_question_is_classified(text):
    assert is_frame_question(text) is True, text


@pytest.mark.parametrize(
    "text",
    [
        "",
        "what is a partial derivative",
        "what colour is the curve in the diagram",
        "how big is the graph",
        "what does it mean",
        "tell me more",
        "is this partial derivatives?",
        "summarise the lesson so far",
        "quiz me on partial derivatives",
        "how do I use this",
        # A reference to a DIFFERENT moment overrides a positive marker, exactly
        # as it does for the recency classifier.
        "what equation is on screen in the first minute",
        "what was on screen earlier",
    ],
)
def test_non_frame_question_is_not(text):
    assert is_frame_question(text) is False, text


def test_frame_classifier_does_not_change_recency_behaviour():
    """The two classifiers are siblings: adding one must not move the other."""
    for text, expected in [
        ("what is on screen right now", True),
        ("what colour is the curve in the diagram", False),
        ("what is a partial derivative", False),
        ("this moment", True),
        ("most recently", True),
        ("what does it say", False),
    ]:
        assert is_recency_seeking(text) is expected, text

    # "what am I looking at" names no recency marker, so recency alone leaves it
    # False -- which is why the service ORs the two classifiers: the spoken audio
    # nearest the playhead is what supplies the frame's explanation.
    assert is_recency_seeking("what am I looking at") is False
    assert is_frame_question("what am I looking at") is True


# ---------------------------------------------------------------------------
# 5. The live frame is ADDED to retrieval, not substituted for it
# ---------------------------------------------------------------------------


@sync_test
async def test_live_frame_is_combined_with_retrieved_passages(chat_env):
    """A frame shows what is displayed; retrieval supplies why. Keep both."""
    audio = FakePassage(
        "c_audio", start_ms=20_000, end_ms=28_000,
        text="the presenter explains dy here",
    )
    service, session_id, provider, _retrieval = chat_env([audio], llm=CiteAllLLM())
    resp = await service.chat(
        session_id=session_id,
        current_time_ms=30_000,
        message="what am I looking at and what does it mean?",
        mode="qa",
        live_frame=make_frame(30_000),
    )
    assert resp.refused is False

    # BOTH are in the model's evidence, with the live frame FIRST so it reads as
    # the high-priority context. Retrieval is not replaced -- it is narrowed to
    # what the store already gated, and the live frame is added in front.
    ids = [p.chunk_id for p in provider.calls[0]]
    assert ids == [live_chunk_id(RESOURCE, 30_000), "c_audio"], ids
    assert provider.calls[0][1].text == "the presenter explains dy here"

    # The prompt names the live frame and tells the model the other passages
    # supply the explanation the frame does not contain.
    assert 'live="true"' in provider.prompt
    assert "EXACT frame" in provider.prompt
    assert "supply the explanation" in provider.prompt

    # A model citing both keeps both citations, and only the live one is flagged.
    assert {c.chunk_id for c in resp.citations} == {
        live_chunk_id(RESOURCE, 30_000),
        "c_audio",
    }
    live_citation = next(c for c in resp.citations if c.live)
    assert live_citation.modality == "visual"
    assert live_citation.start_ms == live_citation.end_ms == 30_000
    audio_citation = next(c for c in resp.citations if not c.live)
    assert audio_citation.live is False
    assert audio_citation.modality == "transcript"
    assert audio_citation.end_ms == 28_000
    assert audio_citation.end_ms <= 30_000


@sync_test
async def test_live_frame_answers_even_when_retrieval_is_empty(chat_env):
    """A captured frame is real evidence, so an empty retrieval is not a refusal.

    Strict-T is not weakened: the passage sits at exactly T, so it is the *newest*
    legal evidence, not evidence from beyond the playhead.
    """
    service, session_id, provider, _retrieval = chat_env([])
    resp = await service.chat(
        session_id=session_id,
        current_time_ms=30_000,
        message="what am I looking at?",
        mode="qa",
        live_frame=make_frame(30_000),
    )
    assert resp.refused is False
    assert [c.chunk_id for c in resp.citations] == [live_chunk_id(RESOURCE, 30_000)]
    assert [p.chunk_id for p in provider.calls[0]] == [
        live_chunk_id(RESOURCE, 30_000)
    ]


@sync_test
async def test_prompt_without_a_live_frame_is_unchanged(chat_env):
    """Additive: no live passage -> no live marker and no live directive."""
    audio = FakePassage("c_audio", start_ms=0, end_ms=10_000)
    service, session_id, provider, _retrieval = chat_env([audio])
    await service.chat(
        session_id=session_id, current_time_ms=60_000,
        message="what is a partial derivative", mode="qa",
    )
    assert 'live="true"' not in provider.prompt
    assert "EXACT frame" not in provider.prompt

    _, direct = build_grounded_prompt("q", [audio], mode="qa")
    assert 'live="true"' not in direct
    assert "EXACT frame" not in direct


# ---------------------------------------------------------------------------
# 6. A document has no frame, and never gets a fabricated one
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "source_type", ["pdf", "pptx", "image", "note", "code", None, "", "document"]
)
def test_document_resource_degrades_honestly(source_type):
    """Only `video` has a frame. Everything else -- and anything unknown -- has none.

    Fail-closed on the unknown case: guessing "probably a video" for an
    unrecognised kind is precisely how a document ends up with an invented frame.
    """
    assert resource_has_frame(source_type) is False
    assert resource_has_frame("video") is True
    assert resource_has_frame("VIDEO") is True


@sync_test
async def test_document_resource_degrades_honestly_in_the_answer(chat_env):
    """A frame request against a document says so, and invents nothing."""
    from app.chat.router import DOCUMENT_NO_FRAME_TEXT

    page = FakePassage(
        "c_page", source_type="pdf", kind=None,
        start_ms=None, end_ms=None, text="partial derivatives on page 3",
    )
    service, session_id, provider, _retrieval = chat_env([page])
    resp = await service.chat(
        session_id=session_id,
        current_time_ms=30_000,
        message="what am I looking at?",
        mode="qa",
        live_frame=None,
        no_frame_note=DOCUMENT_NO_FRAME_TEXT,
    )
    assert resp.refused is False
    assert resp.live_frame == "document"
    assert DOCUMENT_NO_FRAME_TEXT in resp.answer
    assert "partial derivatives on page 3" in resp.answer
    # No live citation, and no fabricated timestamp on the page citation.
    assert all(c.live is False for c in resp.citations)
    assert all(c.end_ms is None for c in resp.citations)
    assert all(c.start_ms is None for c in resp.citations)
    # And no live passage reached the model at all.
    assert all(not p.metadata.get("live") for p in provider.calls[0])


# ---------------------------------------------------------------------------
# 7. Cache: one vision call per (resource, second)
# ---------------------------------------------------------------------------


def test_live_frame_caches_per_second_bucket(tmp_path, monkeypatch):
    """Re-asking inside the same second must not spend another vision call."""
    vision = CountingVision("a slide titled Partial Derivatives")
    monkeypatch.setattr("app.ingestion.visual._gemini_describe", vision)

    first = describe_live_frame(JPEG, resource_id=RESOURCE, at_ms=30_000,
                               cache_dir=tmp_path)
    second = describe_live_frame(JPEG, resource_id=RESOURCE, at_ms=30_999,
                                cache_dir=tmp_path)
    assert first == second
    assert len(vision.calls) == 1, "same second must reuse the cached description"

    # One second later is a different bucket: the frame has changed.
    third = describe_live_frame(JPEG, resource_id=RESOURCE, at_ms=31_000,
                               cache_dir=tmp_path)
    assert third == first
    assert len(vision.calls) == 2

    # A different resource is a different bucket even in the same second.
    describe_live_frame(JPEG, resource_id="res_other", at_ms=30_000,
                        cache_dir=tmp_path)
    assert len(vision.calls) == 3

    # The key really is (resource_id, floor(T/1000)) and nothing else.
    assert frame_cache_key(RESOURCE, 30_000) == frame_cache_key(RESOURCE, 30_999)
    assert frame_cache_key(RESOURCE, 30_000) != frame_cache_key(RESOURCE, 31_000)
    assert frame_cache_key(RESOURCE, 30_000) != frame_cache_key("res_other", 30_000)

    # The cache reused here is the existing app.cache DiskCache, redirected at
    # tmp_path -- so nothing was written into the developer's data/ directory.
    written = list(tmp_path.iterdir())
    assert written, "the cache should have persisted to tmp_path"
    assert all(p.parent == tmp_path for p in written)


def test_live_frame_cache_never_leaks_a_timestamp(tmp_path, monkeypatch):
    """A cached description is reused; the BOUND stamped on it is still the live T.

    The cache saves a vision call. It must never widen a timestamp: the passage
    built from a cached description still carries the caller's own ``at_ms``, so
    asking again 400ms later cannot pull an older frame in as a newer one.
    """
    vision = CountingVision("frame one")
    monkeypatch.setattr("app.ingestion.visual._gemini_describe", vision)
    describe_live_frame(JPEG, resource_id=RESOURCE, at_ms=30_000, cache_dir=tmp_path)
    vision.description = "frame two"
    text = describe_live_frame(JPEG, resource_id=RESOURCE, at_ms=30_400,
                              cache_dir=tmp_path)
    assert text == "frame one"  # reused from the cache
    passage = live_frame_passage(make_frame(30_400, text=text))
    assert passage.end_ms == 30_400
    assert passage.end_ms <= 30_400
    ChromaVectorStore._passes_strict_t(passage, 30_400)


def test_live_frame_never_fabricates_a_description(tmp_path, monkeypatch):
    """An unusable model reply is an error, never a placeholder string.

    Three distinct unusable replies, three distinct refusals:
      * a frame described as blank     -> refused after one call
      * a reply with no frames at all  -> retried by the indexing pass's own
                                         ``_describe_with_retry``, then refused
      * a reply naming an index we never sent -> discarded, never filed anywhere
    """
    blank = CountingVision("no visible content")
    monkeypatch.setattr("app.ingestion.visual._gemini_describe", blank)
    with pytest.raises(LiveFrameUnavailable):
        describe_live_frame(JPEG, resource_id=RESOURCE, at_ms=30_000, cache_dir=tmp_path)
    assert len(blank.calls) == 1
    assert blank.calls[0] == [(0, JPEG)], "only the captured frame may be sent"

    empty_reply = CountingVision("")

    def _empty(frames):
        empty_reply.calls.append(list(frames))
        return {}

    monkeypatch.setattr("app.ingestion.visual._gemini_describe", _empty)
    with pytest.raises(LiveFrameUnavailable):
        describe_live_frame(JPEG, resource_id=RESOURCE, at_ms=30_500, cache_dir=tmp_path)
    assert len(empty_reply.calls) > 1, (
        "an empty reply must be retried before the frame is declared unreadable"
    )

    stray = CountingVision("a description of a frame we never captured")

    def _stray(frames):
        stray.calls.append(list(frames))
        return {7: "a description of a frame we never captured"}

    monkeypatch.setattr("app.ingestion.visual._gemini_describe", _stray)
    with pytest.raises(LiveFrameUnavailable):
        describe_live_frame(JPEG, resource_id=RESOURCE, at_ms=31_000, cache_dir=tmp_path)
    assert len(stray.calls) == 1


def test_live_frame_reuses_the_indexing_pass_vision_call():
    """No second vision client: the live path goes through ``visual._gemini_describe``."""
    import app.ingestion.visual as _visual

    assert "_gemini_describe" in _visual.__dict__
    src = Path(liveframe_mod.__file__).read_text(encoding="utf-8")
    assert "_gemini_describe" in src
    assert "genai.Client" not in src, "the live path must not build its own client"
    assert "generate_content" not in src


# ---------------------------------------------------------------------------
# HTTP surface: the two triggers, the rejection, and the document degradation
# ---------------------------------------------------------------------------


class _IndexedStore:
    """Store double: an indexed source_type plus a fixed retrieval result."""

    def __init__(self, source_type: str, passages: list):
        self._source_type = source_type
        self._passages = passages
        self.queries: list[dict] = []

    def resource_source_type(self, resource_id):
        return self._source_type

    def query(self, query_text, *, course_id, resource_scope, current_time_ms,
              k=8, focus_resource_id=None, prefer_recent=False):
        # Applies the same strict-T predicate the real store does, so the HTTP
        # tests exercise the real gate rather than a permissive double.
        self.queries.append({"current_time_ms": current_time_ms, "k": k})
        return [
            p for p in self._passages
            if p.resource_id in set(resource_scope or [])
            and (p.end_ms is None or p.end_ms <= current_time_ms)
        ][:k]


def _post_frame(client, session_id, token, **fields):
    from app.chat.router import MAX_LIVE_FRAME_BYTES  # noqa: F401 - documented bound

    data = {
        "session_id": session_id,
        "session_token": token,
        "current_time_ms": "30000",
        "message": "what am I looking at?",
        "force_frame": "true",
    }
    data.update(fields)
    return client.post(
        "/chat/live-frame",
        data=data,
        files={"frame": ("frame.jpg", JPEG, "image/jpeg")},
    )


@pytest.fixture
def http_env(monkeypatch):
    """A TestClient over the chat router alone, with every path stubbed.

    Mounted on a bare app so the ``app.main`` lifespan -- which opens the real
    Chroma index under ``data/`` -- never runs.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.chat import router as chat_router
    from app.chat.launch import DEV_SECRET, create_session_token

    monkeypatch.setattr(
        chat_router, "set_service_override",
        lambda service: setattr(chat_router, "_service_override", service),
        raising=True,
    )

    def _build(source_type: str, passages: list, service: ChatService):
        store = _IndexedStore(source_type, passages)
        monkeypatch.setattr(
            chat_router, "get_vector_store", lambda: store, raising=True
        )
        monkeypatch.setattr(
            chat_router, "get_service", lambda: service, raising=True
        )
        monkeypatch.setattr(
            "app.ingestion.liveframe.describe_live_frame",
            lambda jpeg, *, resource_id, at_ms: "a slide titled Partial Derivatives",
        )
        duration_lookups: list[str] = []
        monkeypatch.setattr(
            "app.ingestion.liveframe.resolve_resource_duration_ms",
            lambda rid: duration_lookups.append(rid) or DURATION_MS,
            raising=True,
        )
        monkeypatch.setattr("app.config.settings.lms_launch_secret", DEV_SECRET)

        test_app = FastAPI()
        test_app.include_router(chat_router.router)
        client = TestClient(test_app)
        session = chat_router.session_store.create(
            learner_id="learner_1", course_id=COURSE, resource_id=RESOURCE,
            lesson_id="lesson_1", resource_scope=[RESOURCE], current_time_ms=0,
        )
        token = create_session_token(DEV_SECRET, session.id, session.learner_id)
        return client, session.id, token, duration_lookups

    return _build


def _service(passages, llm=None, monkeypatch=None):
    """A ChatService bound to the router's session store, retrieval seam stubbed.

    It must share the router's ``session_store``: the route looks the session up
    there and the service must see the same object, exactly as the real
    ``get_service()`` does (see tests/conftest.py for why that coupling bites).
    """
    from app.chat import router as chat_router

    retrieval = FakeRetrieval(passages)
    service = ChatService(
        store=_UNUSED_STORE, embedder=None, llm=llm or EchoLLM(),
        sessions=chat_router.session_store, messages=chat_router.message_store,
    )
    if monkeypatch is not None:
        monkeypatch.setattr(
            ChatService, "_retrieval", lambda self: retrieval, raising=True
        )
    return service


def test_out_of_range_frame_is_rejected_over_http(http_env, monkeypatch):
    """The route answers 422 with an honest detail -- no clamp, no silent drop.

    The request carries no duration field at all, so the client has nothing to
    lie with; the bound in the detail is the server's own.
    """
    service = _service([], monkeypatch=monkeypatch)
    client, session_id, token, _lookups = http_env("video", [], service)

    for bad in ("999999999", "655501", "-1"):
        resp = _post_frame(client, session_id, token, current_time_ms=bad)
        assert resp.status_code == 422, (bad, resp.text)
        detail = resp.json()["detail"]
        assert ("past the end" in detail) or ("before the start" in detail), detail
        assert str(DURATION_MS) in detail or bad == "-1"


def test_explicit_button_forces_a_frame_on_regardless_of_wording(http_env, monkeypatch):
    """`force_frame` overrides the classifier, so any wording captures the frame."""
    audio = FakePassage("c_audio", start_ms=20_000, end_ms=28_000,
                        text="the presenter explains dy here")
    service = _service([audio], llm=CiteAllLLM(), monkeypatch=monkeypatch)
    client, session_id, token, _lookups = http_env("video", [audio], service)

    resp = _post_frame(
        client, session_id, token,
        message="explain partial derivatives to me",  # NOT a frame question
        force_frame="true",
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["live_frame"] == "live"
    live_ids = [c["chunk_id"] for c in body["citations"] if c["live"]]
    assert live_ids == [live_chunk_id(RESOURCE, 30_000)]
    cited = next(c for c in body["citations"] if c["live"])
    assert cited["modality"] == "visual"
    assert cited["start_ms"] == cited["end_ms"] == 30_000


def test_frame_question_wordings_reach_the_live_path(http_env, monkeypatch):
    """The second trigger: wording alone, with no button, still captures."""
    service = _service([], monkeypatch=monkeypatch)
    client, session_id, token, _lookups = http_env("video", [], service)

    resp = _post_frame(
        client, session_id, token, message="what am I looking at?", force_frame="false"
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["live_frame"] == "live"


def test_frame_without_button_or_frame_wording_is_refused(http_env, monkeypatch):
    """A frame with neither trigger is a client/server disagreement.

    Refusing loudly beats silently dropping the capture: a dropped frame is
    indistinguishable from "the capture did not work".
    """
    service = _service([], monkeypatch=monkeypatch)
    client, session_id, token, _lookups = http_env("video", [], service)

    resp = _post_frame(
        client, session_id, token,
        message="explain partial derivatives to me", force_frame="false",
    )
    assert resp.status_code == 422, resp.text
    assert "not about the current frame" in resp.json()["detail"]


def test_live_frame_is_combined_with_retrieved_passages_over_http(http_env, monkeypatch):
    """End to end: the answer cites the live frame AND the retrieved audio."""
    audio = FakePassage("c_audio", start_ms=20_000, end_ms=28_000,
                        text="the presenter explains dy here")
    service = _service([audio], llm=CiteAllLLM(), monkeypatch=monkeypatch)
    client, session_id, token, _lookups = http_env("video", [audio], service)

    resp = _post_frame(client, session_id, token, current_time_ms="30000")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    chunk_ids = {c["chunk_id"] for c in body["citations"]}
    assert chunk_ids == {live_chunk_id(RESOURCE, 30_000), "c_audio"}
    for c in body["citations"]:
        assert c["end_ms"] <= 30_000


def test_document_route_makes_no_vision_call(http_env, monkeypatch):
    """A pdf/pptx/image never reaches the vision client, and says so in the answer."""
    page = FakePassage(
        "c_page", source_type="pdf", kind=None,
        start_ms=None, end_ms=None, text="partial derivatives on page 3",
    )
    service = _service([page], llm=CiteAllLLM(), monkeypatch=monkeypatch)
    client, session_id, token, lookups = http_env("pdf", [page], service)

    resp = _post_frame(client, session_id, token)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["live_frame"] == "document"
    assert "no frame to capture" in body["answer"]
    assert all(c["live"] is False for c in body["citations"])
    assert all(c["end_ms"] is None for c in body["citations"])
    # A document has no timeline, so not even a duration lookup was needed.
    assert lookups == []


def test_live_frame_rejects_a_non_resource_in_the_session_grant(http_env, monkeypatch):
    """The frame is evidence about a resource: it must be inside the grant."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.chat import router as chat_router
    from app.chat.launch import DEV_SECRET, create_session_token

    service = _service([], monkeypatch=monkeypatch)
    monkeypatch.setattr(chat_router, "get_vector_store",
                        lambda: _IndexedStore("video", []), raising=True)
    monkeypatch.setattr(chat_router, "get_service", lambda: service, raising=True)
    monkeypatch.setattr("app.config.settings.lms_launch_secret", DEV_SECRET)
    test_app = FastAPI()
    test_app.include_router(chat_router.router)
    client = TestClient(test_app)
    session = chat_router.session_store.create(
        learner_id="learner_1", course_id=COURSE, resource_id=RESOURCE,
        lesson_id="lesson_1", resource_scope=[RESOURCE], current_time_ms=0,
    )
    token = create_session_token(DEV_SECRET, session.id, session.learner_id)

    resp = _post_frame(client, session.id, token, video_id="res_not_granted")
    assert resp.status_code == 403, resp.text
    assert "not authorised" in resp.json()["detail"]
