"""Fewer embeddings per video, and Pinecone Inference as the embedder.

Two changes cut embedding spend: speech cues and frame descriptions are
merged into bigger chunks (``group_timed``), and ``PineconeEmbedder`` moves
the embedding off Gemini's ~1,000 texts/day free tier. Neither may weaken the
strict-T gate, so the merge tests pin the merged ``end_ms`` to the LATEST end.
"""

from __future__ import annotations

import pytest

from app.ingestion import jobs
from app.ingestion.chunking import CHUNK_CHARS, TextSegment
from app.ingestion.embedder import EmbeddingAlignmentError, PineconeEmbedder


def _cue(start_s, end_s, text="word", kind="transcript"):
    return TextSegment(text=text, start_ms=start_s * 1000, end_ms=end_s * 1000, kind=kind)


# -- group_timed ------------------------------------------------------------


def test_cues_within_the_span_merge_and_end_at_the_latest_end():
    merged = jobs.group_timed(
        [_cue(0, 4, "a"), _cue(4, 9, "b"), _cue(9, 12, "c")], max_span_ms=30_000
    )
    assert len(merged) == 1
    assert merged[0].text == "a b c"
    assert (merged[0].start_ms, merged[0].end_ms) == (0, 12_000)


def test_merged_end_is_the_max_even_when_cues_overlap():
    # A long cue followed by a short one inside it: the group must not end at
    # the last cue's end, or the long cue would be visible before it finished.
    merged = jobs.group_timed([_cue(0, 20), _cue(5, 8)], max_span_ms=30_000)
    assert merged[0].end_ms == 20_000


def test_a_group_never_spans_more_than_the_limit():
    cues = [_cue(i * 5, i * 5 + 5) for i in range(20)]  # 100 s of speech
    merged = jobs.group_timed(cues, max_span_ms=30_000)
    assert all(s.end_ms - s.start_ms <= 30_000 for s in merged)
    assert len(merged) == 4
    assert merged[-1].end_ms == 100_000


def test_a_group_fits_one_chunk():
    cues = [_cue(i, i + 1, "x" * 200) for i in range(10)]
    merged = jobs.group_timed(cues, max_span_ms=30_000)
    assert all(len(s.text) <= CHUNK_CHARS for s in merged)
    assert len(merged) > 1


def test_unknown_end_is_never_merged_and_stays_unknown():
    open_cue = TextSegment(text="open", start_ms=4_000, end_ms=None)
    merged = jobs.group_timed([_cue(0, 4), open_cue, _cue(5, 8)], max_span_ms=30_000)
    assert open_cue in merged
    assert all(s.end_ms is not None for s in merged if s is not open_cue)


def test_documents_pass_through_untouched():
    pages = [TextSegment(text=f"p{i}", page_no=i, kind="pdf") for i in range(3)]
    assert jobs.group_timed(pages, max_span_ms=30_000) == pages


def test_kinds_are_not_mixed():
    merged = jobs.group_timed(
        [_cue(0, 2, kind="caption"), _cue(2, 4, kind="transcript")], max_span_ms=30_000
    )
    assert [s.kind for s in merged] == ["caption", "transcript"]


def test_build_chunks_uses_far_fewer_chunks_for_a_lecture():
    # 10 minutes of 5 s cues: 120 cues used to be 120 chunks (120 embeddings).
    cues = [_cue(i * 5, i * 5 + 5, "a short spoken phrase") for i in range(120)]
    chunks = jobs.build_chunks(
        resource_id="r", course_id="c", source_type="video", text_segments=cues
    )
    assert len(chunks) == 20
    assert max(c.end_ms for c in chunks) == 600_000


# -- PineconeEmbedder ---------------------------------------------------------


class _Item:
    def __init__(self, values):
        self.values = values


class _Resp:
    def __init__(self, data):
        self.data = data


class _FakeInference:
    def __init__(self, dim=1024, short=False, width=None):
        self.calls: list[tuple[int, str]] = []
        self.dim, self.short, self.width = dim, short, width

    def embed(self, *, model, inputs, parameters):
        self.calls.append((len(inputs), parameters["input_type"]))
        n = len(inputs) - (1 if self.short else 0)
        return _Resp([_Item([float(i)] * (self.width or self.dim)) for i in range(n)])


class _FakeClient:
    def __init__(self, **kw):
        self.inference = _FakeInference(**kw)


def _embedder(**kw):
    client = _FakeClient(**kw)
    emb = PineconeEmbedder("llama-text-embed-v2", "", client=client, sink=lambda *_: None)
    emb._retry_sleep = lambda _s: None
    return emb, client.inference


def test_documents_are_batched_as_passages():
    emb, api = _embedder()
    vectors = emb.embed_documents([f"t{i}" for i in range(200)])
    assert len(vectors) == 200
    assert api.calls == [(96, "passage"), (96, "passage"), (8, "passage")]


def test_queries_use_query_mode_and_are_cached():
    emb, api = _embedder()
    first = emb.embed_query("what is a partial derivative")
    again = emb.embed_query("what is a partial derivative")
    assert first == again
    assert api.calls == [(1, "query")]


def test_a_short_response_is_refused_not_zipped():
    emb, _ = _embedder(short=True)
    with pytest.raises(EmbeddingAlignmentError):
        emb.embed_documents(["a", "b"])


def test_a_wrong_width_response_is_refused():
    emb, _ = _embedder(width=512)
    with pytest.raises(EmbeddingAlignmentError):
        emb.embed_documents(["a"])


def test_store_refuses_an_index_of_another_width():
    from app.retrieval.pinecone_store import PineconeVectorStore

    class _Index:
        dimension = 3072

        def list(self):
            return iter([])

    emb, _ = _embedder()
    with pytest.raises(RuntimeError, match="3072-d"):
        PineconeVectorStore(index=_Index(), embedder=emb)


def test_config_reports_the_pinecone_width(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "embedding_provider", "pinecone")
    assert settings.expected_embedding_dim() == 1024
