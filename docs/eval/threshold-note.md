# Eval Threshold Sensitivity Note (review M6)

## Proxy gap (read first)

`DEFAULT_THRESHOLD` (`app/eval/harness.py`, currently `0.08`) and the
citation-overlap minimum (`citation_supported(..., min_overlap=2)`) are
**unvalidated heuristic constants** — they have not been tuned against human
relevance judgments.

`should_refuse()` in the harness is a **retrieval-score proxy, not the
production LLM refusal path**. It decides refuse/answer from gated-retrieval
emptiness, gold-visibility, and best-score-vs-threshold only. It never
exercises the production chat path (`app/chat/service.py`: prompt grounding,
citation-allowlist re-parse, unknown-ID refusal, uncited-answer handling) or a
real LLM. Treat harness `refusal_correctness` as a retrieval-gating signal
only; no quality claim about production refusal behavior rests on it.

## Method

Offline run over the 25-pair golden set (`docs/eval/golden.jsonl`, `k=5`),
varying only `--threshold`:

```bash
cd apps/api
python -m app.eval.harness --threshold 0.02
python -m app.eval.harness --threshold 0.08
python -m app.eval.harness --threshold 0.30
```

## Results

| threshold | retrieval_acc | refusal_correctness | leakage_rate | citation_acc | answer items wrongly refused |
|-----------|---------------|---------------------|--------------|--------------|------------------------------|
| 0.02      | 1.0           | 1.0                 | 0.0          | 1.0          | 0 / 16                       |
| 0.08 (default) | 1.0      | 1.0                 | 0.0          | 1.0          | 0 / 16                       |
| 0.30      | 1.0           | 1.0                 | 0.0          | 1.0          | 3 / 16 (eval-vb5, eval-pdf2, eval-f1) |

`production_gap_rate` was `0.0` at all three thresholds. Latency p50 stayed
~7–8 ms across runs (in-process, no network).

## Reading the deltas

- `retrieval_acc` (gold-chunk hit rate) and `refusal_correctness` (refuse-set
  decision rate) are **flat** across this sweep: retrieval ranking is
  threshold-independent, and refuse-expected items refuse before the threshold
  check (gold gated out or unanswerable probe), so the threshold never sees
  them.
- The threshold **does** bite on the answer side: at `0.30`, three
  answer-expected items (`eval-vb5` max_score 0.2704, `eval-pdf2` 0.2722,
  `eval-f1` 0.2163) flip to `refuse` while still retrieving their gold chunks
  (`retrieval_acc` stays 1.0). That is over-refusal the scoreboard's
  `refusal_correctness` does not capture — it only scores the refuse-expected
  subset.
- Takeaway: `0.08` is a safe operating point for this fixture (no answer-side
  flips between `0.02` and `0.08`); raising it toward `0.30` trades answer
  recall for nothing on this set. Re-tune against human judgments before any
  measured quality claim.
