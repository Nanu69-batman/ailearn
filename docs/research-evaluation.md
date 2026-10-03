# Research & Evaluation Plan — AI Learning Assistant

## 1. Test Scenarios

| ID | Scenario | Setup | Expected behavior |
|----|----------|-------|-------------------|
| S1 | Before-timestamp question | Pause at T, ask about content strictly before T | Answer grounded in chunks with end <= T |
| S2 | Later-content question | Ask about material taught after T | Refuse or state not-yet-covered; no leakage |
| S3 | PDF-grounded answer | Question answerable only from permitted PDF | Answer cites PDF page |
| S4 | PPT-grounded answer | Question answerable only from permitted PPT slide | Answer cites slide number |
| S5 | Unanswerable question | Info not present in any permitted resource | Explicit refusal ("not available"), no invention |
| S6 | Multi-turn follow-up | Chain 2-3 connected questions referencing prior turns | Context carried correctly; citations maintained |
| S7 | Citation support check | For each S1-S4 answer, verify cited timestamp/page/slide actually contains the claimed info | Cited source supports claim |
| S8 | Adversarial timestamp probe | Directly ask "what comes next in the video?" or "spoiler the ending" | Refuse / no post-T content |

## 2. Metrics

- **Retrieval accuracy**: % gold-relevant chunks in top-k retrieved set
- **Citation accuracy**: % answers whose cited source genuinely supports the claim (human or LLM-judge verified)
- **Hallucination rate**: % answers containing unsupported claims when refused/grounded answer expected
- **Timestamp leakage rate**: % responses (or retrieved sets) containing any chunk/content with start > T
- **Latency**: p50/p95 end-to-end response time
- **Cost/query**: approximate $ per query (embedding + LLM tokens)

## 3. Golden Dataset Design (20-30 QA pairs)

JSONL schema:
```json
{"id", "pause_at", "question", "type": "video_before|video_later|pdf|ppt|unanswerable|followup|adversarial", "gold_answer", "gold_sources": [{"kind": "video|pdf|ppt", "start", "end"/"page"/"slide"}], "expected": "answer|refuse"}
```
Distribution across ~25 pairs: 6 video_before, 4 video_later, 4 pdf, 3 ppt, 3 unanswerable, 3 follow-up chains, 2 adversarial. Include edge pauses (T near segment boundary, T=0, T=end).

## 4. Timestamp Leakage Test (programmatic)

1. Instrument retrieval: log every chunk used in the prompt/context assembly.
2. Assert: for each retrieved chunk, `chunk.end <= pause_at`.
3. Answer-side check: scan generated answer for video content strings/topics labeled with start > T (e.g., require a labeler or string-match against post-T transcript).
4. Leakage rate = flagged answers / total timestamped queries. Gate: must be 0 on S2/S8.

## 5. Regression Testing

- Freeze the golden dataset and T values in version control; run on every retrieval/prompt/model change.
- Track a scoreboard: retrieval acc, citation acc, hallucination rate, leakage rate, p95 latency, cost.
- CI gate: leakage rate == 0 and refusal correctness on S5 >= 95%; other metrics must not drop >2pp vs baseline.
- Re-run full suite after any change to chunking, embedding model, or reranker.
