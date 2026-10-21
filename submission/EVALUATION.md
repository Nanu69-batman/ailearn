# Evaluation and testing

This page covers what was measured, how, and what the numbers mean. Every number
here comes from a results file in this repo or from a command run on 2026-10-21.
Anything not measured is marked as such.

## Summary

| What | Result | Source |
|---|---|---|
| Spoiler leakage (passages after the playhead in retrieved sets) | **0.0** (0 of 25 questions) | `submission/eval-results/chroma-2026-10-21.json` |
| Production-gap rate (production retrieval vs eval-strict rule) | **0.0** | same |
| Refusal correctness | **1.0** (25/25) | same |
| Citation accuracy | **1.0** | same |
| Retrieval accuracy (answerable questions) | **0.9375** (15/16) | same |
| Retrieval latency, in process | p50 **8.3 ms**, p95 **10.0 ms** | same |
| Backend tests | **479 collected, 479 passed, 0 failed**, no collection errors | `pytest`, 2026-10-21 |
| Frontend type check | pass | `npx tsc --noEmit` |
| Frontend contract check | PASS | `npm run contract-check` |

Embedder used for the eval: the **offline `HashingEmbedder`**. See the caveat in (a).

---

## (a) RAG evaluation

### The golden set

`submission/eval/golden.jsonl` has **25 questions** over a synthetic fixture course,
*Bioenergetics 101* (`submission/eval/fixture-course.md`). The course has a 60-minute
video lecture in 12 five-minute segments, a 6-page PDF handout and a slide
deck. All content is invented so that each fact has exactly one known location.
Every question carries a pause time `pause_at_ms` (the learner's playhead `T`),
the expected behaviour (`answer` or `refuse`), and the gold source location.

| Question type | Expected | Count | What it tests |
|---|---|---|---|
| `video_before` | answer | 6 | The fact was taught before `T`, so it must be found |
| `pdf` | answer | 4 | The fact is only in the PDF (documents are not time-gated) |
| `ppt` | answer | 3 | The fact is only in the slides |
| `followup` | answer | 3 | Follow-up phrasing ("And how does…", "Following up on that…"), asked as a single turn |
| `video_later` | **refuse** | 4 | The fact exists but is taught **after** `T`. Answering would be a spoiler |
| `unanswerable` | **refuse** | 3 | The course never covers it |
| `adversarial` | **refuse** | 2 | Prompts that try to pull in later or outside content |
| **Total** | 16 answer / 9 refuse | **25** | |

### What each metric means

| Metric | Plain meaning | Target |
|---|---|---|
| **Retrieval accuracy** | For answerable questions: was the gold passage among the top `k=5` passages retrieved under the gate? | High |
| **Refusal correctness** | Did the system answer when it should and refuse when it should, across all 25? | 1.0 |
| **Leakage rate** | Share of questions where any retrieved video passage had `end_ms > T` or came from a resource outside the learner's scope. This is the spoiler metric. | **Must be 0** |
| **Citation accuracy** | Do the cited passages actually support the answer (token-overlap check against the gold answer)? | 1.0 |
| **Production-gap rate** | The production retrieval path is re-run over the *full* pool and checked against the same strict rule. A nonzero value means production and the eval disagree, which is a regression. With the old loose gate (`start_ms <= T`) this measured **48%**. | **Must be 0** |
| **Latency p50 / p95** | Wall time of the retrieval step per question, in process (no network, no LLM) | Informational |

### Results

Fresh run on 2026-10-21 (`python -m app.eval.harness --out ../../submission/eval-results/chroma-2026-10-21.json`, from `apps/api`):

| Metric | 2026-10-20 (`chroma-2026-10-20.json`) | **2026-10-21 (`chroma-2026-10-21.json`)** |
|---|---|---|
| Items (answer / refuse) | 25 (16 / 9) | 25 (16 / 9) |
| Retrieval accuracy | 0.9375 | **0.9375** |
| Refusal correctness | 1.0 | **1.0** |
| Leakage rate | 0.0 | **0.0** |
| Citation accuracy | 1.0 | **1.0** |
| Production-gap rate (items) | 0.0 (0) | **0.0 (0)** |
| Latency p50 / p95 | 9.7 ms / 15.9 ms | **8.3 ms / 10.0 ms** |
| Store / k / threshold | Chroma / 5 / 0.08 | Chroma / 5 / 0.08 |
| Embedder | HashingEmbedder (offline) | HashingEmbedder (offline) |

**The one miss:** `eval-f3` (a follow-up question) was refused although it was
answerable. The gold passage was not in the top 5 under the offline embedder
(`max_score` 0.5627). This is an over-refusal, which is the safe direction: no
wrong or spoiling answer was produced.

### Caveats (read before quoting the numbers)

1. **Offline embedder.** The harness always uses the deterministic
   `HashingEmbedder` (1536-d, no network) with a real temporary Chroma store. It
   has no option for the Gemini embedder, and this run made no code change to
   add one. Retrieval accuracy with `gemini-embedding-2` is **not measured**
   (a planned follow-up). Leakage and production gap do not depend on
   the embedder: they check *which* passages may be returned, not how they rank.
2. **Refusal is a retrieval proxy.** The harness's `should_refuse` decides from
   gated-retrieval emptiness and a score threshold (`0.08`). It does not run the
   production LLM prompt, citation re-parse or uncited-answer handling. Those are
   covered by unit tests (see (b)), not by this score. Threshold sensitivity is in
   [eval/threshold-note.md](eval/threshold-note.md).
3. **Small, synthetic set.** 25 questions over invented content. Answer quality
   with the real LLM is checked by live probes (below), not by a large labelled set.
4. `baseline.json` (loose gate, production gap **0.48**) and `baseline-strict.json`
   (strict gate, **0.0**) in `eval-results/` are the 2026-10-03 before/after runs
   behind the 48% figure. They predate a harness rebuild, so their other metrics
   are not comparable with today's.

### Live probes on the real stack

Recorded on the real demo video
(`Partial-derivatives.mp4`, 655.5 s) with Gemini:

| Playhead T | Question | Result | Cited at (ms) | Modality |
|---|---|---|---|---|
| 0:02 | What is written on the board? | Refused (nothing shown yet) | - | - |
| 0:30 | What equation is on screen? | Answered | 28000 | visual |
| 5:00 | what is a partial derivative? | Answered | 23000, 284010 | transcript |
| 8:00 | "Ask about this frame" | Answered, `live: true` | 480000 | visual |
| 10:00 | What colour is the curve? | Refused (the curve is not in the gated evidence) | - | - |

Every citation satisfied `end_ms <= T`.

---

## (b) Testing details

Commands run on 2026-10-21 from `apps/api` with Python 3.14:

```text
$ python -m pytest --co -q -p no:warnings | tail -1
479 tests collected in 1.47s

$ python -m pytest -q -p no:warnings
479 passed in 109.85s (0:01:49)
```

The suite **collects with no errors**. This is checked separately because a
collection error once hid 19 tests behind a count that looked plausible. The
tests use fakes for Gemini, Groq and Whisper and a real Chroma store in a temp
directory. They need no network and no keys.

### What the tests protect

| Area | Test files (`apps/api/tests/`) |
|---|---|
| **Timestamp gate and leakage** (`end_ms <= T`, null end excluded, never `start_ms`, gate inside the store query, recency never widens the pool) | `test_chat_safety.py`, `test_store.py`, `test_live_frame.py` (live frame sits at exactly T), `test_visual.py` (frame times from index arithmetic, not the model), `test_duration.py` (duration is a safety input) |
| **Citations and refusal** (unknown id refused, uncited answer refused after one repair retry, bunched markers validated, citation modality never defaulted, honest refusal texts, grounded vs supplemental) | `test_chat_safety.py`, `test_ta_behaviour.py`, `test_processing_frontier.py` (honest "not processed yet") |
| **Agentic tutor** (the range tool never returns past the playhead, even for "the whole video"; greetings and "who are you" are conversational with no tools and no citations; an invented citation or a provider failure falls back to the classic pipeline; an uncited subject answer is never served; one required retry when the tools are skipped; no general knowledge for a quiz; no raw citation markers in a cut-off reply; intent classification) | `test_agent.py` |
| **Pinecone store** (same gated set as Chroma, strict-T filter sent to Pinecone, Python re-check holds if the server filter leaks, deletes by exact resource id) | `test_pinecone_store.py` |
| **Hosted config** (with no local Whisper fallback, a failed transcription fails the window instead of silently skipping speech) | `test_hosted_config.py` |
| **Scope, session and launch auth** (scope intersected and never widened, `scope_empty` separate from low evidence, frame outside the grant gets 403, session token checked) | `test_chat_safety.py`, `test_live_frame.py`, `test_subjects.py` (`None` vs `[]` scope) |
| **Ingestion and windowing** (120 s windows, chunk ids safe per window, stream/full modes, `/position`, transcript-first stages, empty window advances the frontier, full visual coverage, OCR honesty, routes and job envelope) | `test_ingestion.py`, `test_windowed_ingest.py`, `test_progressive_ingest.py`, `test_staged_ingest.py`, `test_empty_window.py`, `test_processing_frontier.py`, `test_visual_coverage.py`, `test_visual_speed.py`, `test_ocr.py` |
| **Transcription / Groq** (window audio sent to Groq, 429 retried, fallback to local Whisper, times shifted to video time) | `test_windowed_ingest.py` |
| **Embedding and quota handling** (batched embedding with order guards, 429 backoff on the whole batch, spent daily quota fails fast with a readable reason, upload size/length/rate caps) | `test_embedder_batching.py`, `test_quota_exhausted.py`, `test_upload_caps.py`, `test_cache.py` |
| **Subjects and resources** (subject CRUD, attach refuses unknown ids, delete detaches then purges, purge leaves no served bytes) | `test_subjects.py`, `test_resource_delete.py`, `test_resource_purge.py` |
| **Eval harness** (harness end to end over the golden set, leakage detector) | `test_eval_harness.py` |
| **Health** | `test_health.py` |

Gap, stated plainly: the `POST /lms/session` HMAC **signature** check has no
dedicated test file in the current suite. Session-token checks are exercised
through `test_live_frame.py`.

### Frontend checks

Run from `apps/web` on 2026-10-21:

| Command | Result | What it checks |
|---|---|---|
| `npx tsc --noEmit` | **pass** (exit 0) | Types across the Next.js app |
| `npm run contract-check` | **PASS** | That `lib/api.ts` matches the API response shapes, that KaTeX CSS is loaded, that no message path bypasses the Markdown renderer, and that `rehype-raw` is not imported anywhere (model HTML stays inert) |

`npm run build` was not run for this report, because it conflicts with the
running dev server. The last recorded build passed.

---

## (c) Performance

Measured during development. The demo video is
`Partial-derivatives.mp4`, 655.5 s. "By construction" means the figure is derived
from code constants and was not timed.

| Claim | Number | Basis |
|---|---|---|
| Spoilers served in eval | 0 of 25 questions | measured, Chroma store |
| Embedding calls for a 253-text video | 257 before, 4 now | 257 and 7 measured; 4 by construction |
| Vision time for one 2-minute window | 114.9 s down to 54.6 s | measured, one run each, noisy |
| Frame bytes per call | down about 59% | measured |
| Time to first usable answers (stream) | first 6 minutes of video | measured on demo video |
| Transcript-first / overlapped lanes speed-up | not measured yet | tests only, fakes |
| Whisper time for one 2-minute window | about 29 s down to about 15 s | measured, two runs each, loaded machine |
| Groq transcription, one 2-minute window | 2.4-3.4 s | measured, 3 full windows, one run each |

Speech to text per 2-minute window, in detail:

| One 2-minute window, demo video | Time |
|---|---|
| Local Whisper `tiny`, old call | 28-30 s |
| Local Whisper `tiny`, window-only call | 14-18 s |
| Groq `whisper-large-v3-turbo` (decode + upload + reply) | **2.4-3.4 s** |

Vision pass, one 120 s window, 30 frames, real Gemini calls:

| | Before | After |
|---|---|---|
| Total vision time | 114.9 s | 54.6 s (2.1x faster) |
| Frames sent to the model | 30 | 21 (9 skipped as repeats) |
| Average bytes per call | about 244 KB | about 98 KB |
| Calls | 4, one after another | 4, three at a time |

Caveats:
- Single Gemini vision calls ranged from 15 to 89 s across runs, so the vision
  speed-up is noisy.
- The duplicate-frame threshold was tuned on one lecture.
- The Whisper timings were taken with other apps running. In the app, the vision
  lane shares the CPU, and in-app times have not been re-measured.
- The Groq numbers are one run per window. Using Groq sends lecture audio to a
  third party.

Cost levers that are in the code: batched embeddings (64 texts per call), skipping
near-duplicate frames, downscaled JPEG frames (at most 768 px, quality 80), disk
caches for billable calls, transcription offloaded to Groq, and upload caps on
the hosted deployment.

**Not measured:** end-to-end time per window in stream mode on the real stack,
retrieval accuracy with the Gemini embedder, chat latency with the real LLM under
load, concurrent users, and anything on the hosted deployment.

---

## (d) Limitations

- The eval runs offline with a hashing embedder. Production uses Gemini
  embeddings, so retrieval accuracy there is not measured.
- The golden set is small (25) and synthetic. The refusal metric is a
  retrieval-score proxy, not the production LLM refusal path.
- One real lecture (11 minutes) has been exercised end to end. There is no
  long-form video, no long multi-page document, and no multi-subject coverage.
- Ingest throughput is bound by the free tier: about 100 embedding texts per
  minute and about 1,000 embedding requests per day on Gemini. A spent quota
  stops ingest with a `503` until it resets.
- Job state, the in-flight guard, sessions and subjects live in process memory
  (sessions and subjects are mirrored to local files). A restart loses running
  jobs.
- YouTube ingest is unverified here and often blocked from datacenter IPs when
  hosted.
- The LMS launch signature check has no dedicated test.
