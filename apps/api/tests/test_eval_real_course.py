"""The real-course evaluation: exported chunks, embedder cache, end-to-end scoring."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.eval import end_to_end
from app.eval.harness import CachedEmbedder, load_course_chunks, load_golden, run_eval

EVAL = Path(__file__).resolve().parents[3] / "submission" / "eval"
CHUNKS = EVAL / "partial-derivatives-chunks.jsonl"
GOLDEN = EVAL / "golden-partial-derivatives.jsonl"


def test_exported_course_loads_with_both_materials():
    chunks, course_id, allowed = load_course_chunks(CHUNKS)
    assert course_id == "demo"
    assert set(allowed) == {"res_2e629d682a3d", "res_7c1f4a92d0e5"}
    video = [c for c in chunks if c.source_type == "video"]
    pdf = [c for c in chunks if c.source_type == "pdf"]
    assert len(pdf) == 97 and all(c.page_no for c in pdf)
    assert video and all(c.end_ms is not None and c.start_ms <= c.end_ms for c in video)


def test_every_answerable_golden_item_resolves_to_real_chunks():
    from app.eval.harness import resolve_gold_chunk_ids

    chunks, _, _ = load_course_chunks(CHUNKS)
    for item in load_golden(GOLDEN):
        gold = resolve_gold_chunk_ids(item, chunks)
        if item["expected"] == "answer":
            assert gold, item["id"]
        if item["type"] in ("video_later", "adversarial"):
            # A spoiler question's answer really is after the pause.
            assert all(c.end_ms > item["pause_at_ms"] for c in chunks if c.chunk_id in gold)


def test_cached_embedder_embeds_each_text_once():
    calls = []

    class Inner:
        def embed_documents(self, texts):
            calls.append(list(texts))
            return [[float(len(t))] for t in texts]

        def embed_query(self, text):
            calls.append([text])
            return [0.0]

    emb = CachedEmbedder(Inner())
    assert emb.embed_documents(["a", "bb", "a"]) == [[1.0], [2.0], [1.0]]
    assert emb.embed_documents(["bb", "ccc"]) == [[2.0], [3.0]]
    emb.embed_query("q")
    emb.embed_query("q")
    assert calls == [["a", "bb"], ["ccc"], ["q"]]


def test_real_course_offline_run_has_no_leakage():
    board = run_eval(GOLDEN, chunks_path=CHUNKS, embedder="hashing")
    assert board["n_total"] == len(load_golden(GOLDEN))
    assert board["leakage_rate"] == 0.0
    assert board["production_gap_rate"] == 0.0
    assert board["embedder"] == "HashingEmbedder (offline)"


def test_end_to_end_verdicts():
    v = end_to_end.item_verdict
    assert v("answer", "grounded", True) == "correct"
    assert v("answer", "grounded", False) == "answered, other source"
    assert v("answer", "refused", False) == "over-refused"
    assert v("refuse", "refused", False) == "correct"
    assert v("refuse", "supplemental", False) == "correct"
    # "Not there yet" while citing earlier material is correct; revealing is not.
    assert v("refuse", "grounded", False) == "correct"
    assert v("refuse", "grounded", False, ["= 4"]) == "revealed withheld content"


def test_revealed_ignores_spacing_and_latex():
    r = end_to_end.revealed
    assert r(r"So the answer is $1 + \cos(2)$.", ["1 + cos(2)"]) == ["1 + cos(2)"]
    assert r("The lesson has not got there yet.", ["1 + cos(2)", "graphs and slopes"]) == []
    assert r(None, ["x"]) == []


def test_end_to_end_classify_and_scoreboard():
    c = end_to_end.classify
    assert c(SimpleNamespace(refused=True, mode="grounded", citations=[])) == "refused"
    assert c(SimpleNamespace(refused=False, mode="supplemental", citations=[])) == "supplemental"
    assert c(SimpleNamespace(refused=False, mode="grounded", citations=[1])) == "grounded"
    rows = [
        {"expected": "answer", "outcome": "grounded", "gold_cited": True, "verdict": "correct",
         "spoiler_leaks": [], "latency_s": 2.0},
        {"expected": "answer", "outcome": "refused", "gold_cited": False, "verdict": "over-refused",
         "spoiler_leaks": [], "latency_s": 1.0},
        {"expected": "refuse", "outcome": "refused", "gold_cited": False, "verdict": "correct",
         "spoiler_leaks": [], "latency_s": 1.0},
    ]
    board = end_to_end.scoreboard(rows)
    assert board["spoiler_safe_rate"] == 1.0
    assert board["answered_with_gold_citation"] == 0.5
    assert board["over_refused"] == 0.5
    assert board["withheld_correctly"] == 1.0
