"""Store-level enforcement: scope, strict-T, and low-T starvation.

Build-brief tests 3-7:

3. ``test_store_enforces_scope``               -- ungranted resource absent
4. ``test_store_enforces_strict_t_end_ms``     -- post-T video chunk absent
5. ``test_store_excludes_null_end_ms_video``   -- NULL end_ms excluded
6. ``test_store_does_not_time_gate_docs``      -- pdf retrievable at any T
7. ``test_low_t_video_query_not_starved_by_docs`` -- ADR-002 R1 regression

These assert against the REAL Chroma store, not a double. The
``test_chat_safety.py`` module documents that its ``FakeStore`` proves a
contract, not the product; these tests are the product-level evidence that
scope is enforced, which that file explicitly says was missing.
"""

from __future__ import annotations

import pytest

from app.retrieval.store import (
    NO_END_MS,
    TIME_GATED_SOURCE_TYPES,
    build_where,
    chroma_metadata,
)
from tests.store_helpers import make_chunk

T = 120_000
SCOPE = ["res_video", "res_pdf"]


def _ids(passages) -> list[str]:
    return [p.chunk_id for p in passages]


def _naive_top_k(chroma_store, query, scope, t, *, n_results: int) -> list[str]:
    """Query with ``n_results=k`` -- i.e. what the store would do without
    over-fetching. Used to demonstrate that a starvation fixture really does
    starve, so the regression test cannot silently pass on a corpus shape that
    never exhibited the bug."""
    where = build_where(
        course_id="course_1", resource_scope=scope, current_time_ms=t
    )
    got = chroma_store._collection.query(
        query_embeddings=[chroma_store._embedder.embed_query(query)],
        n_results=n_results,
        where=where,
        include=["metadatas"],
    )
    return [m["chunk_id"] for m in (got.get("metadatas") or [[]])[0]]


def test_store_enforces_scope(chroma_store):
    """A chunk from an ungranted resource cannot be returned.

    Two ungranted resources are indexed, and the query grants only one of
    them. If scope were enforced anywhere but in the store query, this is
    where it would show.
    """
    chroma_store.add(
        [
            make_chunk("c_granted", resource_id="res_video"),
            make_chunk("c_hidden_a", resource_id="res_other_video"),
            make_chunk("c_hidden_b", resource_id="res_unrelated"),
        ]
    )

    got = chroma_store.query(
        "partial derivatives",
        course_id="course_1",
        resource_scope=SCOPE,
        current_time_ms=T,
        k=10,
    )

    assert "c_granted" in _ids(got)
    assert "c_hidden_a" not in _ids(got)
    assert "c_hidden_b" not in _ids(got)
    for passage in got:
        assert passage.resource_id in SCOPE


def test_empty_scope_returns_nothing(chroma_store):
    """An empty grant list is fail-closed, not "match everything".

    An empty ``$in`` matching every document would hand the whole corpus to an
    unauthorised caller, so the filter is built to be impossible to satisfy.
    """
    chroma_store.add([make_chunk("c_any")])

    # The predicate itself is unsatisfiable, which is the fail-closed shape.
    where = build_where(course_id="course_1", resource_scope=[], current_time_ms=T)
    assert where["resource_id"]["$in"] == ["__no_granted_resource__"]

    got = chroma_store.query(
        "partial derivatives",
        course_id="course_1",
        resource_scope=[],
        current_time_ms=T,
        k=5,
    )
    assert got == []


def test_cross_course_isolation(chroma_store):
    """A chunk from another course is invisible even with a generous scope."""
    chroma_store.add(
        [
            make_chunk("c_here", course_id="course_1"),
            make_chunk("c_other_course", course_id="course_2"),
        ]
    )
    got = chroma_store.query(
        "partial derivatives",
        course_id="course_1",
        resource_scope=["res_video", "res_unrelated"],
        current_time_ms=T,
        k=10,
    )
    assert _ids(got) == ["c_here"]


def test_store_enforces_strict_t_end_ms(chroma_store):
    """Video chunks ending after T are excluded; ``end_ms``, never ``start_ms``.

    ``c_spanning`` starts at 10s (well before T) and ends at 130s (after T). It
    looks covered and would survive a ``start_ms`` gate -- which is exactly the
    48% spoiler rate ADR-002 §5.1 measured.
    """
    chroma_store.add(
        [
            make_chunk("c_before", start_ms=0, end_ms=100_000),
            make_chunk("c_spanning", start_ms=10_000, end_ms=130_000),
            make_chunk("c_after", start_ms=200_000, end_ms=300_000),
        ]
    )

    got = chroma_store.query(
        "partial derivatives",
        course_id="course_1",
        resource_scope=SCOPE,
        current_time_ms=T,
        k=10,
    )
    ids = _ids(got)

    assert "c_before" in ids
    assert "c_spanning" not in ids, "chunk spanning T leaked (start_ms gate?)"
    assert "c_after" not in ids
    for passage in got:
        assert passage.end_ms is not None and passage.end_ms <= T


def test_strict_t_boundary_is_inclusive(chroma_store):
    """A chunk ending exactly at T is kept -- inclusive comparison."""
    chroma_store.add([make_chunk("c_boundary", start_ms=0, end_ms=T)])
    got = chroma_store.query(
        "partial derivatives",
        course_id="course_1",
        resource_scope=SCOPE,
        current_time_ms=T,
        k=5,
    )
    assert _ids(got) == ["c_boundary"]


def test_store_excludes_null_end_ms_video(chroma_store):
    """NULL ``end_ms`` on a video chunk is EXCLUDE, not UNKNOWN.

    Two ways to be wrong are asserted: the chunk must not come back, AND the
    sentinel must survive the round-trip as a real ``None`` rather than ``-1``
    leaking into a citation. ADR-002 §5.1 explicitly forbids a future
    "simplification" that drops the null check.
    """
    chroma_store.add(
        [
            make_chunk("c_null_end", start_ms=0, end_ms=None),
            make_chunk("c_real", start_ms=0, end_ms=100_000),
        ]
    )

    got = chroma_store.query(
        "partial derivatives",
        course_id="course_1",
        resource_scope=SCOPE,
        current_time_ms=T,
        k=10,
    )
    ids = _ids(got)

    assert "c_null_end" not in ids, "a video chunk with NULL end_ms must be excluded"
    assert "c_real" in ids

    for passage in got:
        assert passage.end_ms != NO_END_MS, "sentinel leaked into a passage"
        assert passage.end_ms is not None


def test_null_end_ms_metadata_becomes_sentinel():
    """Unit-level: unknown end is stored as the sentinel, not omitted."""
    meta = chroma_metadata(make_chunk("c", end_ms=None))
    assert meta["end_ms"] == NO_END_MS

    known = chroma_metadata(make_chunk("c", end_ms=5_000))
    assert known["end_ms"] == 5_000


def test_null_end_ms_excluded_at_any_t(chroma_store):
    """Even at the maximum T a null end is excluded.

    Proves the exclusion comes from the unknown end itself, not from the chunk
    happening to be in the future.
    """
    chroma_store.add([make_chunk("c_null_end", start_ms=0, end_ms=None)])
    got = chroma_store.query(
        "partial derivatives",
        course_id="course_1",
        resource_scope=SCOPE,
        current_time_ms=6 * 60 * 60 * 1000,
        k=5,
    )
    assert got == []


def test_store_does_not_time_gate_docs(chroma_store):
    """pdf/pptx chunks are retrievable at any T, with no timestamps at all.

    The doc chunks here have ``end_ms=None`` -- the realistic shape, and the
    case a naive ``end_ms <= T`` filter would silently destroy. Documents have
    no moment-in-time bound, so T must not apply to them.
    """
    chroma_store.add(
        [
            make_chunk(
                "c_pdf",
                resource_id="res_pdf",
                source_type="pdf",
                start_ms=None,
                end_ms=None,
                page_no=7,
                video_id=None,
                text="the partial derivative of f with respect to x",
            ),
            make_chunk(
                "c_pptx",
                resource_id="res_pdf",
                source_type="pptx",
                start_ms=None,
                end_ms=None,
                slide_no=2,
                video_id=None,
                text="partial derivative definition slide",
            ),
        ]
    )

    for t in (0, 1, T, 6 * 60 * 60 * 1000):
        got = chroma_store.query(
            "partial derivative",
            course_id="course_1",
            resource_scope=SCOPE,
            current_time_ms=t,
            k=10,
        )
        ids = _ids(got)
        assert "c_pdf" in ids, f"pdf gated at T={t}"
        assert "c_pptx" in ids, f"pptx gated at T={t}"
        for passage in got:
            if passage.source_type in ("pdf", "pptx"):
                assert passage.end_ms is None
                assert passage.source_type not in TIME_GATED_SOURCE_TYPES


def test_docs_do_not_leak_time_bounds_into_citations(chroma_store):
    """A doc citation reports no start/end, whatever the caller passes."""
    chroma_store.add(
        [
            make_chunk(
                "c_pdf", resource_id="res_pdf", source_type="pdf",
                start_ms=None, end_ms=None, page_no=1, video_id=None,
                text="derivatives of composite functions",
            )
        ]
    )
    got = chroma_store.query(
        "composite functions",
        course_id="course_1",
        resource_scope=SCOPE,
        current_time_ms=T,
        k=5,
    )
    passage = next(p for p in got if p.chunk_id == "c_pdf")
    assert passage.start_ms is None
    assert passage.end_ms is None
    assert passage.page_no == 1


# ---------------------------------------------------------------------------
# ADR-002 R1: low-T starvation
# ---------------------------------------------------------------------------


def test_low_t_video_query_not_starved_by_docs(chroma_store):
    """Video chunks must survive at low T even with many doc chunks in scope.

    Chroma runs ANN and applies metadata filters afterwards, so a ``k=8``
    query at T=30s against 60 doc chunks (all ungated, all eligible) can be
    consumed entirely by docs and return zero video passages -- even though
    eligible video chunks exist. That is the low-T starvation bug.

    The store over-fetches so the eligible set has candidates, and this test
    is the regression that proves it.

    The corpus shape is the realistic one and the one that actually starves:
    the query text matches the DOCS almost exactly (slide headings restate the
    question), while the video transcript says the same thing in the lecturer's
    own words. So the ``k`` nearest neighbours are all docs, and the eligible
    video chunks rank *below* them. ``_naive_top_k`` demonstrates that a plain
    ``n_results=k`` query returns zero video chunks here; the store's
    over-fetch is what recovers them.
    """
    low_t = 30_000
    query = "partial derivative"
    docs = [
        make_chunk(
            f"doc_{i:03d}",
            resource_id="res_pdf",
            source_type="pdf",
            start_ms=None,
            end_ms=None,
            page_no=i,
            video_id=None,
            text=f"partial derivative -- definition, example {i}, worked example",
        )
        for i in range(60)
    ]
    videos = [
        make_chunk(
            f"vid_{i:03d}",
            start_ms=max(low_t - 5_000 - i * 100, 0),
            end_ms=low_t - i * 100,
            text=(
                "so now if we hold y fixed and slide along the x axis the "
                f"slope here changes accordingly, take {i}"
            ),
        )
        for i in range(10)
    ]
    chroma_store.add(docs + videos)

    # The bug being guarded against, shown rather than asserted in the
    # abstract: a naive n_results=k query sees only docs.
    naive = _naive_top_k(
        chroma_store, query, SCOPE, low_t, n_results=8
    )
    assert not [i for i in naive if i.startswith("vid_")], (
        "the fixture no longer reproduces starvation -- with n_results=k the "
        f"naive query returned {naive}; this test would prove nothing"
    )

    got = chroma_store.query(
        query,
        course_id="course_1",
        resource_scope=SCOPE,
        current_time_ms=low_t,
        k=8,
    )

    ids = _ids(got)
    assert ids, "low-T query returned nothing at all"
    video_ids = [i for i in ids if i.startswith("vid_")]
    assert video_ids, (
        f"low-T starvation: {len(ids)} result(s) returned but no video chunk "
        f"among {ids} despite 10 eligible video chunks in scope"
    )
    # And they must all be genuinely within T, or the fix would have just
    # traded starvation for a spoiler leak.
    for passage in got:
        if passage.source_type in TIME_GATED_SOURCE_TYPES:
            assert passage.end_ms is not None and passage.end_ms <= low_t


def test_starvation_fix_does_not_weaken_the_gate(chroma_store):
    """Over-fetching must not buy starvation relief with extra leakage.

    The pair of tests matters: one proves video chunks come back, this proves
    the widened candidate set did not let a post-T chunk slip in through the
    Python-side filter. Over-fetch is a recall fix; the gate is still absolute.
    """
    low_t = 30_000
    chroma_store.add(
        [
            make_chunk(
                f"doc_{i:03d}", resource_id="res_pdf", source_type="pdf",
                start_ms=None, end_ms=None, page_no=i, video_id=None,
                text="chain rule worked example",
            )
            for i in range(40)
        ]
        + [
            make_chunk(
                f"vid_ok_{i:03d}", start_ms=0, end_ms=low_t - 1_000,
                text="chain rule worked example",
            )
            for i in range(5)
        ]
        # Same text, same resource, same course -- only end_ms differs. Nothing
        # but the filter can tell them apart.
        + [
            make_chunk(
                f"vid_future_{i:03d}", start_ms=low_t, end_ms=low_t + 60_000,
                text="chain rule worked example",
            )
            for i in range(5)
        ]
    )

    got = chroma_store.query(
        "chain rule worked example",
        course_id="course_1",
        resource_scope=SCOPE,
        current_time_ms=low_t,
        k=8,
    )
    ids = _ids(got)

    assert [i for i in ids if i.startswith("vid_ok_")]
    assert not [i for i in ids if i.startswith("vid_future_")], (
        "a post-T chunk survived the over-fetch"
    )


def test_focus_resource_id_narrows_scope(chroma_store):
    """``focus_resource_id`` narrows the grant rather than widening it."""
    chroma_store.add(
        [
            make_chunk("c_a", resource_id="res_video"),
            make_chunk("c_b", resource_id="res_pdf2"),
        ]
    )
    got = chroma_store.query(
        "partial derivatives",
        course_id="course_1",
        resource_scope=SCOPE,
        current_time_ms=T,
        k=10,
        focus_resource_id="res_video",
    )
    assert _ids(got) == ["c_a"]


def test_focus_on_ungranted_resource_returns_nothing(chroma_store):
    """Focusing on a resource outside the grant must not bypass scope.

    ``focus_resource_id`` narrows the query; it is not an authority to read an
    ungranted resource. An intersection, never a replacement.
    """
    chroma_store.add([make_chunk("c_hidden", resource_id="res_secret")])
    got = chroma_store.query(
        "partial derivatives",
        course_id="course_1",
        resource_scope=SCOPE,
        current_time_ms=T,
        k=5,
        focus_resource_id="res_secret",
    )
    assert got == []


def test_passages_carry_every_field_the_chat_layer_reads(chroma_store):
    """Shape contract with ``app.chat``.

    ``_passage_to_citation`` and ``build_grounded_prompt`` read exactly these
    attributes. A renamed field would surface as an AttributeError deep in a
    chat request rather than at index time, so it is asserted here.
    """
    chroma_store.add(
        [
            make_chunk(
                "c_full", start_ms=1_000, end_ms=9_000, page_no=None,
                slide_no=None, video_id="vid_pderiv",
                text="a partial derivative holds y constant",
            )
        ]
    )
    passage = chroma_store.query(
        "partial derivative",
        course_id="course_1",
        resource_scope=SCOPE,
        current_time_ms=T,
        k=1,
    )[0]

    for field in (
        "chunk_id", "resource_id", "source_type", "text", "start_ms",
        "end_ms", "video_id", "page_no", "slide_no", "score",
    ):
        assert hasattr(passage, field), f"passage is missing {field}"

    assert passage.chunk_id == "c_full"
    assert passage.source_type == "video"
    assert passage.start_ms == 1_000
    assert passage.end_ms == 9_000
    assert passage.video_id == "vid_pderiv"
    assert 0.0 < passage.score <= 1.0, "cosine score must land in (0, 1]"


def test_delete_resource_removes_only_that_resource(chroma_store):
    """Re-ingest replaces a resource rather than accumulating duplicates."""
    chroma_store.add(
        [
            make_chunk("c_keep", resource_id="res_video"),
            make_chunk("c_drop", resource_id="res_pdf"),
        ]
    )
    chroma_store.delete_resource("res_pdf")

    got = chroma_store.query(
        "partial derivatives",
        course_id="course_1",
        resource_scope=SCOPE,
        current_time_ms=T,
        k=10,
    )
    assert _ids(got) == ["c_keep"]


def test_assert_no_leakage_raises_on_a_leaked_passage():
    """The assertion layer must be loud, never a silent repair.

    Feeds it a hand-built leak (as if the filter regressed) and requires an
    ``AssertionError``. If this ever returned quietly, a filter regression
    would be invisible in production.
    """
    from app.retrieval.store import RetrievedPassage, assert_no_leakage

    scope_leak = RetrievedPassage(
        chunk_id="c", resource_id="res_not_granted", course_id="course_1",
        source_type="video", text="t", start_ms=0, end_ms=1_000, score=1.0,
    )
    with pytest.raises(AssertionError, match="scope leak"):
        assert_no_leakage([scope_leak], resource_scope=SCOPE, current_time_ms=T)

    future_leak = RetrievedPassage(
        chunk_id="c", resource_id="res_video", course_id="course_1",
        source_type="video", text="t", start_ms=0, end_ms=500_000, score=1.0,
    )
    with pytest.raises(AssertionError, match="strict-T leak"):
        assert_no_leakage([future_leak], resource_scope=SCOPE, current_time_ms=T)

    null_leak = RetrievedPassage(
        chunk_id="c", resource_id="res_video", course_id="course_1",
        source_type="video", text="t", start_ms=0, end_ms=None, score=1.0,
    )
    with pytest.raises(AssertionError, match="NULL end_ms"):
        assert_no_leakage([null_leak], resource_scope=SCOPE, current_time_ms=T)


@pytest.mark.parametrize("source_type", sorted(TIME_GATED_SOURCE_TYPES))
def test_every_gated_source_type_is_actually_gated(chroma_store, source_type):
    """Membership of ``TIME_GATED_SOURCE_TYPES`` is not decorative.

    Visual chunks are folded into ``video`` at ingest time, so this is the
    guard against a future gated type being added to the set without a store
    test -- or, worse, a video-typed chunk silently escaping the gate.
    """
    chroma_store.add(
        [
            make_chunk(
                f"c_{source_type}", source_type=source_type,
                start_ms=0, end_ms=T + 60_000, text="gated content marker",
            )
        ]
    )
    got = chroma_store.query(
        "gated content marker",
        course_id="course_1",
        resource_scope=SCOPE,
        current_time_ms=T,
        k=5,
    )
    assert _ids(got) == [], f"{source_type} escaped the strict-T gate"
