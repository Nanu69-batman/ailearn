# claims.md: defence sheet for Malai Pioneer

Companion to `PROJECT.md`. This is not a summary. It is the sheet you rehearse from when
a technical panel asks "how do you know?".

**How to use it.** Section 1 is the claim list. Section 2 is the per-claim script:
rehearse the "If challenged" and "Do not say" lines for each claim you put on a slide.
Section 3 is the question bank, hard ones included. Section 4 is the numbers: if you
cannot say the condition, do not say the number. Section 5 is what to volunteer before
you are caught by it.

**One rule for the whole panel.** If a command disagrees with a slide, the command wins
and the slide is wrong. Say so out loud. It buys more credibility than any number here.

---

## 1. Headline claims

| # | Claim | Evidence | Proof | Caveat |
|---|---|---|---|---|
| 1 | Answers only from what has already played, at or before playhead T | Gate keeps a passage only when `end_ms <= T`; refusals verified at T=2000 and T=600000 ms | `apps/api/app/retrieval/service.py` | 2 videos of 11 min. No long-form test |
| 2 | Every claim carries a concrete millisecond range | 6 probe rows, all citations satisfy `end_ms <= T`, ids validated against the retrieved set | `apps/api/app/chat/service.py` | Proves the passage exists, not that it answers well |
| 3 | Every citation says spoken or on-screen | `transcript` / `visual` tags; unknown provenance maps to null, never defaulted | `PROJECT.md` feature 3 | Provenance metadata, not a confidence score |
| 4 | Uncovered questions get a refusal, not an invention | T=2000 refusal, zero citations, `grounded: false` | Live probe, `PROJECT.md` section 6 | Over-rigid. Refused "explain eigenvalues" at T=30000 |
| 5 | A live frame is answered with a citation exactly at the playhead | T=480000 answered, citation 480000, `live: true`, `modality: visual` | Live probe, `PROJECT.md` section 6 | One probe, one moment. Not a repeated measurement |
| 6 | The no-spoiler rule is structural, not prompt-level | Gate sits before reordering, before the supplemental decision, before any model call | `PROJECT.md` section 10 | Only as good as the `end_ms` values in the index |
| 7 | Visual coverage is 100%, not just the first minute | 164 sampled frames across 655.5 s produced 174 visual chunks, 100% reported | `VisualPassReport` on `res_2e629d682a3d` | One video. A silent 24% cap shipped once before the report existed |
| 8 | Timestamps come from audio signal or frame arithmetic, never the model | 3 guards: duration overshoot rejection, 2-level index validation, no character-splitting of instants | `apps/api/app/ingestion/visual.py` | Discards model indices that may often be right |
| 9 | Tests, types, build and contract check are green and rerunnable | 299 pytest pass, ruff clean, `tsc --noEmit` clean, build pass, contract-check PASS with 77 assertions | See `PROJECT.md` section 12 | Green tests have hidden real bugs here before |
| 10 | Nothing in the index was invented | Failed image description fails the job; degradation stated, never inferred | `apps/api/app/ingestion/images.py` | Costs visible failures. That is the trade |
| 11 | Ingest is multi-format: video, PDF, PPTX, image | pypdf and python-pptx; images ungated, cited by `image_no` | `apps/api/app/ingestion/images.py` | Document path not exercised at scale |
| 12 | LaTeX renders and model HTML stays inert | react-markdown + remark-math + rehype-katex, no `rehype-raw`, KaTeX `trust: false` | `npm run contract-check`; `components/MarkdownBody.tsx` | A grep plus assertions, not a security audit |
| 13 | Unsigned launch is a demo setting, not a deployment mode | `ALLOW_UNSIGNED_LAUNCH=true` and `LMS_LAUNCH_SECRET=dev-secret` are localhost-demo only | `PROJECT.md` limitation 6 | HMAC enforcement is `[PLANNED]`. Do not imply it exists |

---

## 2. Per-claim defence cards

### C1. Only what has been played at or before T

**Claim.** The tutor answers only from what the student has already heard or seen.
**Evidence.** The gate drops every passage where `end_ms > T`. Probe at T=2000 ms: "what is written on the board" REFUSED, because at two seconds nothing has been said. Probe at T=600000 ms: "what colour is the curve" REFUSED, because the curve appears early. Every cited passage in the probe set satisfies `end_ms <= T`. No breaches observed.
**Prove.** `apps/api/app/retrieval/service.py`. Live: ask about the board at T=2 s. Full predicate in `PROJECT.md` section 10, including the null-`end_ms` EXCLUDE sentinel.
**If challenged.** The gate is a filter over index entries, not a prompt instruction, so the model never receives a future passage. It compares `end_ms`, not `start_ms`, so a passage that ends after T is dropped even if it starts before. That bluntness causes some avoidable refusals. It fails safe.
**Do not say.** Not proven for all media types. Not tested at lecture scale. Two 11-minute videos.

### C2. Millisecond citations

**Claim.** Every claim I make points at a moment in the video.
**Evidence.** Probe citations: 28000, 23000, 284010, 480000. Every returned id is checked against the retrieved set. An answer with no citation, or an unknown id, is refused rather than passed through.
**Prove.** `apps/api/app/chat/service.py`, citation validation step, with `PROJECT.md` section 6.
**If challenged.** What is proven is that each cited passage was genuinely retrieved and genuinely ends before T. What is not proven is that it is the best evidence for the sentence, so a citation can be real and still a weak link.
**Do not say.** Do not call this citation accuracy. We verified provenance, not entailment.

### C3. Modality: transcript or visual

**Claim.** It tells you whether it heard it or saw it.
**Evidence.** Two provenance sources: whisper segment bounds for audio, frame-index arithmetic for visuals. Unknown provenance maps to null and is never defaulted to "transcript".
**Prove.** Probe table: "what equation is on screen" at T=30000 cited 28000 as `visual`; "what is a partial derivative" at T=300000 cited 23000 and 284010 as `transcript`.
**If challenged.** Provenance comes from the indexer, not the model, so a mislabelled chunk surfaces as a wrong modality with no error raised. `VisualPassReport` catches a whole-pass failure, not per-chunk labels.
**Do not say.** Not a confidence measure.

### C4. Refusal instead of invention

**Claim.** If the lecture has not got there yet, it says so rather than making it up.
**Evidence.** T=2000 refusal, zero citations, `grounded: false`. T=600000 refusal for an early-video curve. The refusal path emits mode grounded, `grounded: false`, zero citations.
**Prove.** Live probe, or the refusal branch in `apps/api/app/chat/service.py`.
**If challenged.** Originally yes. "Explain eigenvalues" at T=30000 was refused even though the video covers eigenvalues later. Grounded versus Supplemental answering has since shipped and that case is now handled, but the anti-over-refusal boundary is still the fragile part of the design. Same mechanism as the T=2000 refusal, behaving too bluntly. It is the top roadmap item and the fix, Supplemental answering, is designed but not landed. I would rather show you that row than hide it.
**Do not say.** Do not say the refusal rate is optimal or that anti-over-refusal is solved. Supplemental answering reduces the problem but does not eliminate it.

### C5. Live frame at the playhead

**Claim.** If you ask what is on screen right now, it answers about that exact frame.
**Evidence.** Client canvas capture at the playhead, modelled as a passage with `start_ms == end_ms == T`, so it satisfies the gate by equality and the gate needs no special case. Probe at T=480000 returned a citation of exactly 480000, `live: true`, `modality: visual`. Server validates T against the container-header duration and returns 422 out of range, never clamping.
**Prove.** Live probe, or `apps/api/app/ingestion/liveframe.py`.
**If challenged.** The exactness is structural: equality at the gate means a live frame can only ever release itself. The real risk was in the browser, not the gate. The page is `:3000` and media is `:8123`, so `toBlob` needs `crossOrigin="anonymous"` on the `<video>` element, and without it capture fails silently while every test passes.
**Do not say.** 480000 is one probe. Do not present it as a repeated measurement.

### C6. The gate is structural

**Claim.** The no-spoiler rule is enforced in code, not by asking the model nicely.
**Evidence.** Order of operations: scope intersection, Chroma query, strict-T gate, recency reorder on survivors only, prompt from survivors, model call, citation validation. Recency can only reorder, never reintroduce. `assert_no_leakage` re-checks every returned passage and raises on a leak.
**Prove.** `PROJECT.md` section 10 flow. `apps/api/app/retrieval/recency.py` for the reorder.
**If challenged.** Structure buys the guarantee but not the data. A wrong `end_ms` in the index is a structural leak no downstream filter catches. That is why the two indexer guards in D3 exist, and I would treat those as the real correctness surface rather than the gate itself.
**Do not say.** Not end-to-end spoiler-proof. The retrieval step is provable; the index feeding it is guarded, not perfect.

### C7. 100% visual coverage

**Claim.** The slide content is indexed across the whole video, not just the start.
**Evidence.** 164 sampled frames across 655.5 s produced 174 visual chunks. `VisualPassReport` reported status complete at 100% coverage.
**Prove.** The ingest job's `VisualPassReport` for `res_2e629d682a3d`: status, coverage percentage, warnings.
**If challenged.** The report exists because the opposite happened. A hardcoded `max_visual_frames=40` with a `break` covered only 0 to 156 s of a 655 s video, so 24% coverage, and the entire pass sat inside `except Exception: log.warning`. The job reported success and the suite was green. A judge watching the product would have seen 76% of the video invisible with no way to know. The 100% is measured, but recently, and the lesson is that a count alone never proved it.
**Do not say.** Do not say 24% of a video was visibly broken in a demo. It was index-time degradation that reported success.

### C8. Timestamp authority

**Claim.** The model describes; it never places anything in time.
**Evidence.** Three real bugs. Gemini's audio timeline overshot the real duration, now rejected outright. A frame sampled at 650 s but labelled `index: 5` would have been stored at `start_ms=20000` and released by the gate at T=20 s, a spoiler leak through the anti-spoiler component; indices are now validated at two levels against the sampler's real indices, and unknown indices are dropped and recorded, never clamped. Character-proportion splitting was applied to frame descriptions and produced a 135 ms citation reading "with respect to $y$:", the trailing 3.4% of the parent cut mid-sentence; instants are no longer split.
**Prove.** `apps/api/app/ingestion/visual.py`.
**If challenged.** The trade is deliberate. We discard model-supplied indices that may often be right, and we reject a whole Gemini audio timeline that overshoots, throwing away good segments to protect the duration bound. Cheap correctness over clever accuracy.
**Do not say.** Do not say we measured how often model indices would have been right. We did not.

### C9. Quality gates

**Claim.** The test suite, types, build and contract check are green, and you can rerun them.
**Evidence.** 299 pytest pass, ruff clean. `tsc --noEmit` clean. `npm run build` pass. Contract check PASS, 77 assertions.
**Prove.** pytest in `apps/api`. tsc, build, contract-check in `apps/web`. Commands listed in `PROJECT.md` section 12. Offer to run them live.
**If challenged.** Green tests have repeatedly hidden real bugs here: a missing React import passed `tsc`, a ChatPanel prefix bug shipped past a green suite, and the 24% visual cap shipped with the suite fully green. That is why live probes are mandatory and why `PROJECT.md` exists on paper. I treat the suite as necessary, not sufficient.
**Do not say.** Do not say the suite proves the product works. It proves specific invariants.

### C10. No invented content

**Claim.** Nothing in the index was made up.
**Evidence.** No fabricated titles, descriptions or timestamps. A failed image description fails the job rather than indexing invented text. Documents degrade explicitly when there is no frame to capture.
**Prove.** `apps/api/app/ingestion/images.py`, plus the `VisualPassReport` warning list.
**If challenged.** The trade is more visible failures. A job that fails loudly costs a retry. A job that succeeds with invented content costs the trust of everyone who reads a citation. I would rather show you a failed ingest than a plausible answer.
**Do not say.** Do not say answers can never be wrong. The model can still misread a passage it was given.

### C11. Multi-format ingest

**Claim.** It is not video-only. PDFs, slides and images go in too.
**Evidence.** pypdf and python-pptx for documents. Images stored ungated, because null `end_ms` is the exclude sentinel and gating would make an image permanently invisible. Image citations carry `image_no`, not a timestamp.
**Prove.** `apps/api/app/ingestion/images.py`, and the document branch in `PROJECT.md` section 3.
**If challenged.** The honest asymmetry is that document chunks are not time-bounded, so the gate does not apply to them. For a PDF page that is correct. But if a document and a video both cover a topic, the document evidence is not filtered by T.
**Do not say.** Do not say the document path is load tested. It is not.

### C12. LaTeX without raw HTML

**Claim.** Math renders, and the model's HTML cannot execute.
**Evidence.** react-markdown with remark-math and rehype-katex. No `rehype-raw`, so raw HTML in model output is never parsed. KaTeX `trust: false`.
**Prove.** `npm run contract-check` in `apps/web`, which walks `app/`, `components/` and `lib/` for `rehype-raw` imports as part of its 77 assertions. `apps/web/components/MarkdownBody.tsx`.
**If challenged.** This is a deliberate default plus a grep guard, not a security audit of the rendering stack. Dropping `rehype-raw` costs some formatting fidelity and buys inert output.
**Do not say.** Not XSS-audited.

### C13. Demo-only launch settings

**Claim.** The unsigned launch you see is a local demo setting. Production needs HMAC-signed launches and that is not built yet.
**Evidence.** `ALLOW_UNSIGNED_LAUNCH=true` and `LMS_LAUNCH_SECRET=dev-secret` are localhost-demo only, documented as a hard boundary in `PROJECT.md` limitation 6. Gemini keys and the launch secret live in `apps/api/.env`, which is never committed.
**Prove.** `PROJECT.md` section 8 item 6 and section 10 item 5.
**If challenged.** If the question is whether this could go to real students tomorrow, the answer is no. HMAC enforcement is priority 7 on the roadmap. I am telling you now rather than after you find it.
**Do not say.** Do not say the LMS integration is production-ready.

---

## 3. Anticipated judge questions

### Architecture and RAG

**Q. Why not put the video in a long context window?** There is no video endpoint, so
you transcribe and caption first, which is the same ingestion work without the index.
Then it costs more per query and returns a blended paragraph with no way to point at
the 28th second. Time-bounded chunks are the address, and the gate becomes a filter over
addresses. A 10-minute lecture is roughly 253 chunks.

**Q. Why not filter in the prompt, "only use the first N minutes"?** The model would
see the future text and could use it. The gate removes future passages before any model
call. A prompt is not a boundary.

**Q. Why not a hosted RAG platform with built-in video handling?** It would not expose a
gate we control. Policy we cannot edit is not a policy. Libraries do transcription,
vision, chunking, vector storage, parsing and decode. We kept two things in house: the
strict-T gate and citation validation.

**Q. Why Chroma now and not Postgres plus pgvector?** Chroma is what is wired and what
the 2-video demo runs on. Sessions are in memory too. Postgres plus pgvector is
priority 3, so sessions and vectors outlive a restart.

### Correctness and the timestamp gate

**Q. How do you know it does not spoil later content?** Structurally, the gate drops any
passage whose `end_ms` is after T, so the model never receives it. Then
`assert_no_leakage` re-checks every returned passage and raises if one leaked. Probes:
refused at T=2000 ms, and refused at T=600000 ms for a question answered early in the
video.

**Q. Does that fully prevent spoilers?** No, and this is the honest limit. The gate
removes future passages; the remaining risk is the model inferring a conclusion from
earlier material. That is handled at prompt level with a suppression clause, deliberately
narrow so a learner asking "what is a partial derivative" at 0:30 gets an answer. It is
mitigation, not a proof.

**Q. Why `end_ms` and not `start_ms`?** `start_ms` lets a passage ending well after T
through, and the rest of it is future content. An earlier eval harness measured a 48%
production-gap rate with a loose gate. `end_ms <= T` is the only comparison in the filter.

**Q. What happens when `end_ms` is null?** It is the EXCLUDE sentinel, not a wildcard, so
the chunk is dropped. The one exception is images, stored ungated because a gated image
would be permanently invisible; image citations carry `image_no`.

**Q. What if the model returns a citation id it was never given?** The answer is refused.
Every returned id must be in the retrieved set and at least one citation must exist. A
validation failure never escalates to a supplemental answer, because that would launder a
broken answer into a plausible one.

**Q. How do you know the timestamps are real and not model-invented?** Transcripts get
timestamps from the whisper signal itself, frames from frame-index arithmetic, `index *
interval`. Gemini only describes. Two guards exist because this was violated: an
overshooting audio timeline is rejected against the container-header duration, and a frame
index that is not a real sampled index is dropped and recorded.

**Q. Can a student get content they were not granted?** The requested focus resource is
intersected with the granted scope, never replaced. Replacing it was a real past bug. An
empty intersection gives `scope_empty`, separating "your grant does not cover this" from
"you have not watched enough".

**Q. What about PDFs and slides, which have no timeline?** Not time-gated at all, treated
like a page. An honest asymmetry, not an oversight.

### Evaluation

**Q. How do you know the answers are right?** Honestly, we know they are grounded, not
that they are correct. What we verify is provenance: the cited passage exists, ends before
T, and carries its modality. The 6 live probes are the behaviour evidence, and the
refusal rows are the strongest, because a refusal cannot be wrong in the direction that
matters.

**Q. What is your test coverage?** 299 pytest passing, ruff clean, plus `tsc --noEmit`,
`npm run build`, and a contract check with 77 assertions. Commands are in `PROJECT.md`
section 12. Run them yourself.

**Q. Is that circular, tests written by the same people?** Fair, and green tests have
hidden real bugs here more than once. The 24% silent visual cap shipped with the suite
fully green, because the visual pass sat inside `except Exception: log.warning`. That is
why coverage is now reported as data instead of inferred from a count.

**Q. What is the 48% production-gap number?** From an earlier eval harness that measured
the real impact of a loose gate before the strict predicate existed. Historical, not
produced by this repo's current suite.

**Q. Did you compare against a baseline, plain RAG or a plain LLM?** No. There is no
head-to-head evaluation. I would rather tell you than imply one exists.

### Cost and latency

**Q. How do you control Gemini spend?** Shared bounded retries in one place, describe
passes paced under the vision rate limit, live frames cached per (resource, second) so
re-asking the same moment does not re-hit the model, and free-tier 429s handled as
expected: wait about 90 s, retry once, never retry-loop.

**Q. Why does ingest take minutes?** Measured: a 7-minute video took over 7 minutes,
dominated by embedding. Root cause: `batchEmbedContents` given N texts returns one pooled
vector for the lot, not N. The embedder found this and fell back to one HTTP call per
chunk, roughly 253 sequential calls for an 11-minute video. Against the free tier's 100
requests per minute floor, that is a hard floor of about 2.5 minutes for embeddings
alone.

**Q. Is batching live?** Not as an optimisation. The shipped path is per-chunk embedding
with bounded retry. The batched implementation was designed, started and reverted; it
stands as a known issue and a planned fix. The fix calls `embedContent` with an array of
N sub-requests, the shape the REST body `{"requests": [...]}` actually batches on, and
verifies `len(embeddings) == len(sent)` before indexing. If that predicate fails it
raises rather than indexing, because every downstream consumer zips vectors to chunks
positionally and a mis-ordered response would attach the wrong vector to the wrong chunk
with no error anywhere.

**Q. Why not just parallelise the embedding calls?** Batching is fewer requests carrying
more work, parallelism is more requests in flight. This was a batching failure, so more
parallelism would only have made the rate limiter angrier.

**Q. What does a single student question cost?** I do not have a measured per-question
figure. Live frames are cached, but cost per question is not instrumented.

**Q. What is the query latency?** Not separately measured. The gate, scope intersection
and citation validation are local and cheap next to one Gemini answer call, but I have
not put a number on the end-to-end path.

### Scale and limits

**Q. What happens with a 2-hour lecture?** Unknown, and I will not pretend otherwise.
Two videos of 11 minutes is all we have run end to end. No long-form, no multi-subject
matrix, no load testing.

**Q. How many videos have you tested?** Two, both about 11 minutes, both in one subject,
"Engineering Mathematics". One is fully characterised at 89 transcript and 174 visual
chunks over 655.5 s.

**Q. Can it handle many students at once?** Sessions live in a single in-memory store
with no load test, so I cannot answer that. Postgres plus pgvector is planned for exactly
this reason.

**Q. Does it survive a restart?** No. Sessions are in memory and a restart loses them.
Stated in the limitations rather than papered over.

**Q. Does YouTube ingest work?** Unverified. Captions and download are unavailable here, so
that path has never been exercised.

### Data and privacy

**Q. What data leaves the machine?** Audio and sampled frames go to Gemini for vision
descriptions, and transcript plus frame-description text goes to the Gemini embedding
endpoint. Frames captured live at the playhead go to Gemini for description. Chroma and
faster-whisper run locally. No account data, email or profile leaves the machine in this
build.

**Q. Where are the API keys?** `apps/api/.env`. Never committed, never echoed, never read
into a brief. Invariant 5 in `PROJECT.md`.

**Q. Is the launch signed?** Not in this demo. Unsigned launch and the dev secret are
localhost-demo settings, production requires real HMAC, and that enforcement is not built
yet. A hard boundary, not a backlog nicety.

**Q. Can one student see another student's session?** Sessions are addressed by token and
the effective scope is intersected with the grant, so retrieval cannot reach outside it.
With no multi-user load test and in-memory sessions, I would not make a strong isolation
claim under concurrency.

**Q. Can a lecturer delete a video and its chunks?** Purge capability exists and removes
chunks plus the staged asset, but there is no HTTP surface and no subject detach. Today
the honest answer is no delete button.

### Product

**Q. What happens when the question is not covered by the video?** Today it refuses: mode
grounded, `grounded: false`, zero citations. The designed answer is Supplemental
answering, teaching from first principles and labelling the reply as not from the video,
with zero citations and every citation marker stripped. That is `[PLANNED]`, reachable on
two narrow triggers only: an explicit request for outside help, or a grounded refusal
that found no coverage.

**Q. Why did it refuse "explain eigenvalues" at 30 seconds?** Because the gated pool at
30 s did not cover it, though the video covers eigenvalues later. Same mechanism as the
T=2000 refusal, too blunt. Number-one roadmap item.

**Q. Why trust this over a general chatbot?** A general chatbot will use the part of the
lecture you have not reached, with no way to check. Here the answer either carries a
timestamp you can seek to, or it is a refusal. The value is not a better answer, it is
an auditable one.

**Q. Is this better than re-reading notes?** At the point where you are stuck
mid-lecture, yes, because it is bounded by exactly what you have seen. For revision
afterwards a plain summary is better and cheaper, and I would not claim otherwise.

**Q. Who is this for?** A student working through lecture material at their own pace,
pausing to ask at the point of confusion, who needs the answer constrained to their
current position. Not a revision tool, not a search tool.

**Q. What is the hardest thing left to fix?** Over-rigid grounding. Every refusal is safe
and every refusal is also a bad experience, and the fix must be careful, because a
supplemental path that fires too eagerly re-opens the exact spoiler hole the gate closed.
That is why the anti-spoiler suppression and the "never escalate a validation failure to
supplemental" rule belong to the same design.

---

## 4. Numbers you must not misquote

| Figure | Exact meaning | Conditions | Flag |
|---|---|---|---|
| 299 | pytest tests passing | full run in `apps/api`, ruff clean | Verified live on 2026-10-15. If asked, rerun pytest and quote the live number |
| 77 | contract-check assertions | `npm run contract-check` in `apps/web` | measurement |
| 100% | visual coverage of `res_2e629d682a3d` | 164 sampled frames across 655.5 s, from `VisualPassReport` | measurement, one video only |
| 164 | sampled frames | fixed stride over 655.5 s | measurement |
| 174 | visual chunks | from those frames, spanning 0 to 652000 ms | measurement |
| 89 | transcript chunks | spanning 0 to 656380 ms | measurement |
| 655.5 s | duration of `res_2e629d682a3d` | container header via PyAV | measurement |
| 2 | videos exercised end to end | both about 11 min, one subject | the hard coverage limit. Volunteer it |
| ~253 | chunks for an 11-minute video | the per-chunk embedding call count in D2 | **estimate, and it is not 89 + 174 = 263.** Quote "roughly 250 to 260" or just use 263 |
| 263 | 89 + 174 | total indexed chunks for the demo resource | measurement. Prefer this over 253 |
| 7 min / over 7 min | ingest wall time for a 7-minute video | dominated by per-chunk embedding, free tier rate limited | measurement |
| ~2.5 min | hard floor for embeddings alone | 253 sequential calls against the 100 requests/min free-tier floor | **derived estimate, not a stopwatch measurement** |
| 100/min | Gemini free-tier embedding request floor | the limit being hit | external limit, not our measurement |
| 15/min | vision request limit | describe passes are paced under it | external limit |
| ~90 s | wait before one retry on a 429 | then retry once, never retry-loop | policy constant, not a measurement |
| 24% | visual coverage under the old hardcoded cap | `max_visual_frames=40` at 4 s covered 0 to 156 s of a 655 s video | measurement of a fixed bug. Never present as current coverage |
| 156 s | where the old cap stopped | 40 frames at a 4 s stride | measurement of the bug |
| 135 ms | length of the bad split citation | trailing 3.4% of a parent frame description, cut mid-sentence | measurement of the bug |
| 3.4% | trailing share cut off | from character-proportion splitting | measurement of the bug |
| 20000 ms | where a frame sampled at 650 s would have landed | had a model supplied `index: 5` at a 4 s stride | hypothetical. Label it as such |
| 48% | production-gap rate with a loose gate | earlier eval harness, before the strict predicate | historical measurement from a prior harness, not from this repo's current suite |
| 2000 ms | T, first refusal probe | "what is written on the board", refused, zero citations | measurement |
| 30000 ms | T, answered equation probe | cited 28000, visual | measurement |
| 600000 ms | T, curve refusal probe | answer is early in the video, refused | measurement |
| 300000 ms | T, partial derivative probe | cited 23000 and 284010, transcript | measurement |
| 480000 ms | T, live frame probe | cited exactly 480000, `live: true`, visual | measurement, single probe |
| 8123 / 3000 | API and web ports | local dev | configuration |
| 24% vs 100% | do not confuse these | one is the old silent bug, one is the current measured state | mixing them up is the fastest way to lose a panel |

---

## 5. Known limitations to volunteer

Offer these before a judge finds them. Each is in `PROJECT.md` too; this is the short
version to keep in your head.

| # | Limitation | One-line answer if pressed |
|---|---|---|
| 1 | Only two 11-minute videos end to end, one subject, no load testing | "Two videos. That is the number." Correct anyone who says production grade |
| 2 | Over-rigid grounding, "explain eigenvalues" refused at T=30000 | Largely addressed by Supplemental answering (shipped). The residual risk is the anti-over-refusal boundary. Supplemental answering is designed, not landed |
| 3 | Ingest over 7 min for a 7-min video | Per-chunk embedding, cause identified, fix has the predicate `len(embeddings) == len(sent)` |
| 4 | Sessions in memory | A restart loses them. Postgres + pgvector is priority 3 |
| 5 | YouTube ingest unverified | Captions and download unavailable in this environment |
| 6 | Unsigned launch is a demo setting | Production needs real HMAC enforcement and it does not exist yet |
| 7 | No delete-resource surface | Purge capability exists, no endpoint, no subject detach, no ownership check |
| 8 | Cross-origin canvas fails silently | `:3000` page and `:8123` media need `crossOrigin="anonymous"`; without it capture fails while every test passes |
| 9 | The gate is only as good as the `end_ms` values in the index | Two indexer guards exist precisely because that was violated before |
| 10 | Document chunks are not time-gated | Correct for a PDF page, but "only what has been played" is a video property, not a source-agnostic one |
| 11 | Green tests have hidden real bugs here repeatedly | Say this first. It reframes the suite as necessary and not sufficient |
| 12 | No baseline, no accuracy benchmark, no measured cost or latency | These do not exist. Offer the coverage matrix as the next step |

---

## 6. The three that always come

1. **"What did you build that a framework would not have given you?"** The strict-T gate,
   plus validating citations against the retrieved set. Everything else is a library.
2. **"What is the worst number in your project?"** Over 7 minutes of ingest for a
   7-minute video. Per-chunk embedding, cause identified, fix has a verification predicate.
3. **"What next, in order?"** Supplemental answering, with anti-spoiler suppression
   landing alongside it. Then batched embedding, then persistence, then the coverage matrix.