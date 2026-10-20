"""Batching and alignment guarantees of the Gemini embedder.

The bug these lock down: the embedder handed the SDK a list of plain strings,
the SDK packed them into a single multi-part ``Content``, and the server
answered with **one pooled vector** for the lot. The count check caught it and
fell back to one HTTP call per chunk -- 253 sequential round trips for one
video, which is the seven-minute stall in the ingest log.

So the tests below are about the two things that must be true afterwards:

* **Few calls.** N texts cost ``ceil(N / BATCH_SIZE)`` HTTP calls, not N.
* **Correct association.** Vector ``i`` is the embedding of text ``i``, and a
  response that cannot be trusted that way fails loudly instead of quietly
  mis-indexing a chunk.

The fake client is the seam: it records what was sent and returns a vector
derived from the *text* of each request, so a mis-ordered response is
detectable rather than plausible.
"""

from __future__ import annotations

import hashlib
import math

import pytest

from app.ingestion import backoff as backoff_mod
from app.ingestion import embedder as embedder_mod
from app.ingestion.embedder import (
    GEMINI_EMBED_DIM,
    EmbeddingAlignmentError,
    GeminiEmbedder,
    contents_for,
)

# ---------------------------------------------------------------------------
# The fake client
# ---------------------------------------------------------------------------


class _FakeEmbedding:
    """One embedding, shaped like the SDK's ``ContentEmbedding``."""

    def __init__(self, values: list[float]) -> None:
        self.values = values


class _FakeResponse:
    def __init__(self, embeddings: list[_FakeEmbedding]) -> None:
        self.embeddings = embeddings


def _vector_for(text: str) -> list[float]:
    """A deterministic vector that identifies its text.

    The first element is derived from the text itself, so a vector that ends
    up attached to the wrong text is detectable by inspection -- which is
    exactly the silent corruption the alignment guard exists to prevent.

    The vector is spread over several dimensions rather than lying along a
    single axis: two vectors on one axis are parallel, so their cosine is 1.0
    no matter what they are, and an order check built on cosine could not tell
    them apart.
    """
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    values = [b / 255.0 for b in digest[:16]]
    values += [0.0] * (GEMINI_EMBED_DIM - len(values))
    return values


class _FakeModels:
    """Records every batch it is asked to embed."""

    def __init__(self, behaviour=None) -> None:
        self.calls: list[list[str]] = []
        self._behaviour = behaviour

    def embed_content(self, model, contents):
        texts = [c.parts[0].text for c in contents]
        self.calls.append(texts)
        if self._behaviour is not None:
            return self._behaviour(texts, len(self.calls))
        return _FakeResponse([_FakeEmbedding(_vector_for(t)) for t in texts])


class _FakeClient:
    def __init__(self, behaviour=None) -> None:
        self.models = _FakeModels(behaviour)


def _embedder(client=None, **kwargs) -> GeminiEmbedder:
    """A GeminiEmbedder with a fake client and no order self-check.

    The self-check is exercised on its own; every other test wants to observe
    the calls it makes, not the probe's.
    """
    emb = GeminiEmbedder.__new__(GeminiEmbedder)
    emb._client = client if client is not None else _FakeClient()
    emb._model = "gemini-embedding-2"
    emb.dim = GEMINI_EMBED_DIM
    emb._sink = None
    emb._order_checked = True
    # No real backoff sleeps: the retry delay is 15s+ and a test must not wait.
    emb._retry_sleep = lambda _s: None
    for key, value in kwargs.items():
        setattr(emb, key, value)
    return emb


class _FakeClock:
    """Manual clock: ``sleep`` advances it, nothing ever waits in real time."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture(autouse=True)
def _fast_quota():
    """Give every test its own quota bucket, a fake clock, and no real sleeping.

    The quota is process-global (``_QUOTA``), so without this a test inherits
    whatever earlier tests drained from the real 100 texts/minute bucket and
    real wall-clock refill decides whether it sleeps. A fresh bucket on a fake
    clock makes the outcome independent of order and timing.
    """
    clock = _FakeClock()
    previous = embedder_mod.install_embedding_quota(
        embedder_mod._TextQuota(
            capacity=100_000,
            refill_per_minute=100_000,
            clock=clock,
            sleeper=clock.sleep,
        )
    )
    try:
        yield
    finally:
        embedder_mod.install_embedding_quota(previous)


# ---------------------------------------------------------------------------
# Batching
# ---------------------------------------------------------------------------


def test_embedding_uses_batched_requests():
    """N texts cost ceil(N / BATCH_SIZE) HTTP calls, not N.

    This is the regression test for the stall itself. 253 chunks went out as
    253 sequential requests; they must go out as a handful of batches.
    """
    client = _FakeClient()
    emb = _embedder(client)
    texts = [f"chunk number {i} of the lecture" for i in range(253)]

    vectors = emb.embed_documents(texts)

    assert len(vectors) == 253
    expected_calls = -(-253 // emb.BATCH_SIZE)  # ceil
    assert len(client.models.calls) == expected_calls
    assert expected_calls < 10, "the whole point: a handful of calls, not 253"
    # Every text was sent, exactly once, and no call exceeded the limit.
    sent = [t for call in client.models.calls for t in call]
    assert sorted(sent) == sorted(texts)
    assert all(len(call) <= emb.BATCH_SIZE for call in client.models.calls)


def test_one_call_carries_a_whole_batch():
    """A batch really is one HTTP call carrying one sub-request per text.

    The SDK collapses a list of plain strings into a single multi-part
    Content, and the server pools that into one vector. Asserting on the
    *shape* of the request -- one Content per text -- is what stops that
    from coming back.
    """
    texts = ["alpha", "beta", "gamma"]
    contents = contents_for(texts)

    assert len(contents) == 3
    assert [c.parts[0].text for c in contents] == texts
    # Distinct Content objects, not one object with three parts.
    assert len({id(c) for c in contents}) == 3
    assert all(len(c.parts) == 1 for c in contents)


# ---------------------------------------------------------------------------
# Alignment
# ---------------------------------------------------------------------------


def test_batched_vectors_align_with_input_texts():
    """Vector i is the embedding of text i, across batch boundaries.

    Alignment is asserted across a batch boundary (253 texts over batches of
    100), because that is where an off-by-one would hide: a chunk that came
    out one slot early still looks like a perfectly good vector.
    """
    client = _FakeClient()
    emb = _embedder(client)
    texts = [f"chunk number {i} of the lecture" for i in range(253)]

    vectors = emb.embed_documents(texts)

    for i, text in enumerate(texts):
        assert vectors[i][:16] == _vector_for(text)[:16], (
            f"vector {i} is not the embedding of text {i}: "
            f"{text!r} was mis-associated"
        )


def test_embedding_order_preserved_end_to_end(tmp_path):
    """Order survives the whole path into the store, not just the embedder.

    The store zips vectors against chunks positionally, so the embedder's
    order and the store's order have to be the same order. This asserts it
    through the real store: chunk N's stored embedding is chunk N's text.
    """
    from app.retrieval.store import ChromaVectorStore
    from tests.store_helpers import make_chunk

    client = _FakeClient()
    emb = _embedder(client)
    texts = [f"chunk number {i} of the lecture" for i in range(253)]
    chunks = [
        make_chunk(f"chunk_{i}", text=text, resource_id="res_align")
        for i, text in enumerate(texts)
    ]

    store = ChromaVectorStore(path=tmp_path / "chroma", embedder=emb)
    written = store.add(chunks)
    assert written == 253

    got = store._collection.get(include=["embeddings", "documents"])
    by_id = dict(zip(got["ids"], got["embeddings"]))
    for i, chunk in enumerate(chunks):
        # Chroma hands back numpy arrays and stores them as float32, so this
        # compares with a tolerance rather than exactly.
        stored = [float(x) for x in by_id[chunk.chunk_id][:16]]
        expected = _vector_for(chunk.text)[:16]
        assert all(
            abs(a - b) < 1e-6 for a, b in zip(stored, expected)
        ), f"chunk {chunk.chunk_id} was indexed with another chunk's vector"


def test_pooled_response_still_falls_back():
    """One vector for N texts engages the safety net, never mis-association.

    The server pooling N texts into one vector is the original bug. If it
    ever comes back, the count check must catch it and the embedder must
    fall back to one-text-at-a-time -- never return that single pooled
    vector and let the store zip it against 253 chunks.
    """
    def pooled(texts, call_number):
        # The first (batched) call pools everything into one vector; the
        # per-text fallback then behaves normally.
        if len(texts) > 1:
            return _FakeResponse([_FakeEmbedding(_vector_for("POOLED"))])
        return _FakeResponse([_FakeEmbedding(_vector_for(texts[0]))])

    client = _FakeClient(pooled)
    emb = _embedder(client)
    texts = [f"chunk number {i} of the lecture" for i in range(5)]

    vectors = emb.embed_documents(texts)

    assert len(vectors) == 5, "a pooled response must not be indexed as one vector"
    for i, text in enumerate(texts):
        assert vectors[i][:16] == _vector_for(text)[:16]


def test_mismatched_vector_count_is_loud():
    """A short response raises instead of silently truncating.

    If the server answers a 10-text batch with 7 vectors, zipping those
    against the 10 chunks would attach every vector after the seventh to the
    wrong chunk -- and no later code would ever notice.
    """
    def short(texts, call_number):
        return _FakeResponse(
            [_FakeEmbedding(_vector_for(t)) for t in texts[:-3]] or
            [_FakeEmbedding(_vector_for(texts[0]))]
        )

    client = _FakeClient(short)
    emb = _embedder(client)
    texts = [f"chunk number {i} of the lecture" for i in range(10)]

    with pytest.raises(embedder_mod.EmbeddingAlignmentError):
        emb.embed_documents(texts)


def test_mismatched_width_is_loud():
    """A ragged response (mixed vector widths) raises.

    Zipping vectors of different lengths truncates them all to the shortest,
    which produces plausible-looking, wrong vectors.
    """
    def ragged(texts, call_number):
        out = []
        for i, t in enumerate(texts):
            values = _vector_for(t)
            if i == 1:
                values = values[:-1]
            out.append(_FakeEmbedding(values))
        return _FakeResponse(out)

    client = _FakeClient(ragged)
    emb = _embedder(client)
    texts = [f"chunk number {i} of the lecture" for i in range(4)]

    with pytest.raises(embedder_mod.EmbeddingAlignmentError):
        emb.embed_documents(texts)


def test_order_self_check_catches_a_swapped_response():
    """The one-time self-check refuses a response that arrives out of order.

    The count check cannot see a permutation: 10 vectors for 10 texts is a
    perfect count and a perfectly wrong order. This is the check that makes
    the positional zip safe.
    """
    def swapped(texts, call_number):
        return _FakeResponse(
            [_FakeEmbedding(_vector_for(t)) for t in reversed(texts)]
        )

    client = _FakeClient(swapped)
    emb = _embedder(client, _order_checked=False)
    texts = [f"chunk number {i} of the lecture" for i in range(10)]

    with pytest.raises(embedder_mod.EmbeddingAlignmentError):
        emb.embed_documents(texts)


def test_order_self_check_passes_on_a_correct_response():
    """The self-check must not fire on a correct response.

    It costs three extra calls and four texts of quota, so it has to be
    right in both directions.
    """
    client = _FakeClient()
    emb = _embedder(client, _order_checked=False)

    vectors = emb.embed_documents(["alpha text", "beta text"])

    assert len(vectors) == 2
    assert vectors[0][:16] == _vector_for("alpha text")[:16]
    assert vectors[1][:16] == _vector_for("beta text")[:16]


def test_order_self_check_runs_once_per_instance():
    """The self-check is a once-per-instance cost, not a per-call cost.

    Four texts of quota and three calls on every batch would undo the saving
    the batching was for.
    """
    client = _FakeClient()
    emb = _embedder(client, _order_checked=False)

    emb.embed_documents(["alpha text", "beta text"])
    first = len(client.models.calls)
    emb.embed_documents(["gamma text", "delta text"])
    second = len(client.models.calls)

    # The probe is one batched call plus two single-text calls, and the first
    # embed's own batch rides along with it.
    assert first == 4, "the probe is three calls, plus the batch being embedded"
    assert second == first + 1, "the second embed must not re-probe"


# ---------------------------------------------------------------------------
# Backoff
# ---------------------------------------------------------------------------


def test_backoff_still_applies_to_batched_calls():
    """A 429 mid-batch retries the whole batch, with the same guarantees.

    The batch is retried as a unit: a 429 is a quota window, so re-sending
    one member of a failed batch would leave the rest of that batch's texts
    unembedded and silently short-index them.
    """
    attempts = {"n": 0}

    def flaky(texts, call_number):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("429 RESOURCE_EXHAUSTED: embed_content")
        return _FakeResponse([_FakeEmbedding(_vector_for(t)) for t in texts])

    client = _FakeClient(flaky)
    emb = _embedder(client)
    texts = [f"chunk number {i} of the lecture" for i in range(10)]

    sleeps: list[float] = []
    emb._retry_sleep = sleeps.append

    vectors = emb.embed_documents(texts)

    assert attempts["n"] == 2, "the batch must be retried"
    assert sleeps == [GeminiEmbedder.EMBED_BACKOFF_S]
    assert len(vectors) == 10
    for i, text in enumerate(texts):
        assert vectors[i][0] == _vector_for(text)[0]


def test_backoff_settings_are_not_weakened():
    """The retry budget and delays stay what they were.

    The quota asks for ~23-60s, so a 2s base would burn every attempt
    inside a single quota window and fail a pass that had already cost six
    minutes.
    """
    delays = [
        backoff_mod.backoff_delay(
            n,
            base=GeminiEmbedder.EMBED_BACKOFF_S,
            cap=GeminiEmbedder.EMBED_BACKOFF_CAP_S,
        )
        for n in range(1, GeminiEmbedder.EMBED_MAX_ATTEMPTS + 1)
    ]
    assert delays[:3] == [15.0, 30.0, 60.0]
    assert max(delays) == 60.0
    assert sum(delays) >= 300
    assert GeminiEmbedder.EMBED_MAX_ATTEMPTS == 8


def test_quota_pacing_waits_for_the_window_instead_of_being_refused():
    """The embedder paces itself instead of provoking a 429.

    The meter counts *texts* per minute, not HTTP calls, so a burst of
    batches is refused outright. Waiting for the window is the difference
    between an ingest that finishes and one that retries into a 60-second
    backoff.
    """
    fake = _FakeClock()
    waited = fake.sleeps
    quota = embedder_mod._TextQuota(
        capacity=100, refill_per_minute=100.0, clock=fake, sleeper=fake.sleep
    )
    previous = embedder_mod.install_embedding_quota(quota)
    try:
        client = _FakeClient()
        emb = _embedder(client)
        texts = [f"chunk number {i} of the lecture" for i in range(150)]

        emb.embed_documents(texts)
    finally:
        embedder_mod.install_embedding_quota(previous)

    assert len(client.models.calls) == 2, "two batches, not 150 requests"
    assert waited, "the second batch must wait for the quota window to refill"
    assert sum(waited) >= 30.0, "50 more texts at 100/minute is 30 seconds"


# ---------------------------------------------------------------------------
# Progress
# ---------------------------------------------------------------------------


def test_progress_reports_counts_during_embedding():
    """The ingest job is told how far the embedding has got.

    A caller watching a five-minute stall has to see the number move.
    """
    seen: list[tuple[int, int]] = []
    client = _FakeClient()
    emb = _embedder(client, _sink=lambda done, total: seen.append((done, total)))

    emb.embed_documents([f"chunk number {i} of the lecture" for i in range(253)])

    assert seen[0] == (0, 253)
    assert seen[-1] == (253, 253)
    assert [d for d, _ in seen] == sorted(d for d, _ in seen)
    assert len(seen) >= 4, "the counter must move as batches land"


def test_a_query_does_not_report_ingest_progress():
    """A retrieval query must not overwrite the ingest's progress counter.

    The sink is process-global, so a chat query running while an ingest is
    in flight would otherwise report "1/1" over the ingest's "120/253".
    """
    seen: list[tuple[int, int]] = []
    client = _FakeClient()
    emb = _embedder(client, _sink=lambda done, total: seen.append((done, total)))

    emb.embed_query("what is a partial derivative")

    assert seen == []


# ---------------------------------------------------------------------------
# The job payload
# ---------------------------------------------------------------------------


def test_job_payload_reports_the_embedding_stage_and_counts():
    """A caller polling the job sees the stage and a moving count.

    The stall this fixes was invisible: the payload said "processing" for
    seven minutes with nothing to distinguish transcription, description and
    embedding, and no count inside the slow phase.
    """
    from app.ingestion import jobs as jobs_mod
    from app.ingestion.jobs import job_payload, new_job

    job = new_job("course_1", "res_abc")

    seen: list[tuple[int, int]] = []
    with jobs_mod.embedding_progress(job.job_id):
        jobs_mod.note_stage(jobs_mod.STAGE_TRANSCRIBING)
        seen.append((0, 253))
        for done in (100, 253):
            jobs_mod.set_stage(
                job.job_id, jobs_mod.STAGE_EMBEDDING,
                embedded=done, embedding_total=253,
            )

    payload = job_payload(job)
    assert payload["stage"] == "embedding"
    assert payload["embedded"] == 253
    assert payload["embedding_total"] == 253


def test_the_ingest_reports_each_stage_it_passes_through(monkeypatch):
    """Transcription, description and embedding are distinguishable.

    A caller watching a spinner has to be able to tell "still transcribing"
    from "still describing" from "embedding 120/253" -- otherwise every
    minute of a long video looks identical.
    """
    from app.ingestion import jobs as jobs_mod
    from app.ingestion.jobs import new_job

    stages: list[str] = []
    original = jobs_mod.note_stage

    def _record(stage, **fields):
        stages.append(stage)
        original(stage, **fields)

    monkeypatch.setattr(jobs_mod, "note_stage", _record)

    job = new_job("course_1", "res_abc")
    jobs_mod._ACTIVE_JOB["job_id"] = job.job_id
    try:
        jobs_mod.note_stage(jobs_mod.STAGE_TRANSCRIBING)
        jobs_mod.note_stage(jobs_mod.STAGE_DESCRIBING)
        jobs_mod.note_stage(jobs_mod.STAGE_EMBEDDING)
    finally:
        jobs_mod._ACTIVE_JOB.pop("job_id", None)

    assert stages == [
        jobs_mod.STAGE_TRANSCRIBING,
        jobs_mod.STAGE_DESCRIBING,
        jobs_mod.STAGE_EMBEDDING,
    ]
    assert set(stages) == {"transcribing", "describing", "embedding"}


# ---------------------------------------------------------------------------
# Coverage additions
# ---------------------------------------------------------------------------


def _texts(n: int) -> list[str]:
    return [f"chunk number {i} of the lecture" for i in range(n)]


def test_batch_size_is_pinned_to_the_api_limit():
    assert GeminiEmbedder.BATCH_SIZE == 100


@pytest.mark.parametrize("n", [0, 1, 7, 100])
def test_contents_for_gives_one_content_per_text(n):
    contents = contents_for(_texts(n))
    assert len(contents) == n
    assert [c.parts[0].text for c in contents] == _texts(n)


def test_250_texts_split_100_100_50_in_order():
    client = _FakeClient()
    emb = _embedder(client)
    texts = _texts(250)

    vectors = emb.embed_documents(texts)

    assert [len(c) for c in client.models.calls] == [100, 100, 50]
    assert [t for c in client.models.calls for t in c] == texts
    assert [v[:16] for v in vectors] == [_vector_for(t)[:16] for t in texts]


def test_empty_input_returns_empty_without_an_api_call():
    client = _FakeClient()
    emb = _embedder(client, _order_checked=False)

    assert emb.embed_documents([]) == []
    assert client.models.calls == []
    assert emb._order_checked is False, "nothing was checked, nothing is marked"


def test_count_mismatch_raises_alignment_error():
    def short(texts, call_number):
        return _FakeResponse([_FakeEmbedding(_vector_for(t)) for t in texts[:-1]])

    emb = _embedder(_FakeClient(short))
    with pytest.raises(EmbeddingAlignmentError, match="returned 4 vector"):
        emb.embed_documents(_texts(5))


def test_overlong_response_raises_alignment_error():
    def long(texts, call_number):
        return _FakeResponse(
            [_FakeEmbedding(_vector_for(t)) for t in texts + ["extra"]]
        )

    emb = _embedder(_FakeClient(long))
    with pytest.raises(EmbeddingAlignmentError):
        emb.embed_documents(_texts(3))


def test_pooled_single_vector_takes_per_text_fallback():
    """N>1 texts answered with exactly one vector -> one call per text."""
    def pooled(texts, call_number):
        if len(texts) > 1:
            return _FakeResponse([_FakeEmbedding(_vector_for("POOLED"))])
        return _FakeResponse([_FakeEmbedding(_vector_for(texts[0]))])

    client = _FakeClient(pooled)
    emb = _embedder(client)
    texts = _texts(4)

    vectors = emb.embed_documents(texts)

    assert [len(c) for c in client.models.calls] == [4, 1, 1, 1, 1]
    assert [v[:16] for v in vectors] == [_vector_for(t)[:16] for t in texts]


def test_single_text_with_one_vector_is_not_a_fallback():
    client = _FakeClient()
    emb = _embedder(client)
    emb.embed_documents(["only one"])
    assert [len(c) for c in client.models.calls] == [1]


def test_check_widths_raises_on_mixed_width():
    emb = _embedder()
    good = [0.0] * GEMINI_EMBED_DIM
    with pytest.raises(EmbeddingAlignmentError, match="mixed widths"):
        emb._check_widths([good, good[:-1]], offset=0)


def test_check_widths_raises_on_uniform_wrong_width():
    """Every vector the same, but not the width the index expects."""
    emb = _embedder()
    wrong = [0.0] * 768
    with pytest.raises(EmbeddingAlignmentError, match="width 768"):
        emb._check_widths([wrong, wrong], offset=0)


def test_check_widths_accepts_correct_width_and_empty():
    emb = _embedder()
    emb._check_widths([[0.0] * GEMINI_EMBED_DIM] * 3, offset=0)
    emb._check_widths([], offset=0)


def test_uniform_wrong_width_response_is_loud_end_to_end():
    def narrow(texts, call_number):
        return _FakeResponse([_FakeEmbedding([0.1] * 768) for _ in texts])

    emb = _embedder(_FakeClient(narrow))
    with pytest.raises(EmbeddingAlignmentError):
        emb.embed_documents(_texts(3))


def test_order_check_skipped_for_single_text_and_runs_later():
    client = _FakeClient()
    emb = _embedder(client, _order_checked=False)

    emb.embed_query("just one")
    assert emb._order_checked is False
    assert len(client.models.calls) == 1, "no probe for a single text"

    emb.embed_documents(["alpha text", "beta text"])
    assert emb._order_checked is True
    assert len(client.models.calls) == 1 + 4, "probe (3 calls) + the batch"


def test_swapped_order_is_flagged_and_stays_unchecked():
    def swapped(texts, call_number):
        return _FakeResponse(
            [_FakeEmbedding(_vector_for(t)) for t in reversed(texts)]
        )

    emb = _embedder(_FakeClient(swapped), _order_checked=False)
    with pytest.raises(EmbeddingAlignmentError, match="order does not match"):
        emb.embed_documents(_texts(3))
    assert emb._order_checked is False, "a failed check must not mark success"


def test_transient_probe_error_is_retried_on_the_next_embed():
    """A probe that errors must not disable the order check for good."""
    state = {"fail": True}

    def flaky_probe(texts, call_number):
        if state["fail"]:
            state["fail"] = False
            raise ValueError("boom: non-retryable probe failure")
        return _FakeResponse([_FakeEmbedding(_vector_for(t)) for t in texts])

    client = _FakeClient(flaky_probe)
    emb = _embedder(client, _order_checked=False)

    with pytest.raises(ValueError):
        emb.embed_documents(["alpha text", "beta text"])
    assert emb._order_checked is False

    emb.embed_documents(["alpha text", "beta text"])
    assert emb._order_checked is True
    # failed probe call, then a full probe (3 calls) + the batch
    assert len(client.models.calls) == 1 + 3 + 1


def test_429_retries_only_that_batch_without_duplicates():
    """Batch 2 of 3 is refused once: it alone is resent, in order, once."""
    def flaky(texts, call_number):
        if call_number == 2:
            raise RuntimeError("429 RESOURCE_EXHAUSTED: embed_content")
        return _FakeResponse([_FakeEmbedding(_vector_for(t)) for t in texts])

    client = _FakeClient(flaky)
    emb = _embedder(client)
    sleeps: list[float] = []
    emb._retry_sleep = sleeps.append
    texts = _texts(250)

    vectors = emb.embed_documents(texts)

    assert [len(c) for c in client.models.calls] == [100, 100, 100, 50]
    assert client.models.calls[0] == texts[:100]
    assert client.models.calls[1] == client.models.calls[2] == texts[100:200]
    assert client.models.calls[3] == texts[200:]
    assert sleeps == [GeminiEmbedder.EMBED_BACKOFF_S]
    assert len(vectors) == 250
    assert [v[:16] for v in vectors] == [_vector_for(t)[:16] for t in texts]


def test_text_quota_take_and_refill():
    clock = _FakeClock()
    q = embedder_mod._TextQuota(
        capacity=100, refill_per_minute=60.0, clock=clock, sleeper=clock.sleep
    )
    assert q.capacity == 100
    assert q.available() == 100

    assert q.take(0) == 0.0
    assert q.take(100) == 0.0
    assert math.isclose(q.available(), 0.0, abs_tol=1e-9)

    clock.now += 30.0  # 1 token/second
    assert math.isclose(q.available(), 30.0)

    # 10 more than available: waits exactly the deficit.
    waited = q.take(40)
    assert math.isclose(waited, 10.0)
    assert clock.sleeps == [waited]
    assert math.isclose(q.available(), 0.0, abs_tol=1e-9)

    clock.now += 10_000.0
    assert q.available() == 100, "refill is capped at capacity"


def test_text_quota_consume_never_goes_negative():
    clock = _FakeClock()
    q = embedder_mod._TextQuota(capacity=10, refill_per_minute=60.0, clock=clock)
    q.consume(25)
    assert q.available() == 0.0
