# Techniques — what we built and why it holds up

Talking points for the presentation. Every number below was measured on the
real stack unless marked otherwise; "by construction" means derived from the
code's constants and not timed live.

Demo video: `Partial-derivatives.mp4`, 655.5 s, a whiteboard lecture.

---

## 1. Spoiler-proof answers: the strict timestamp gate

**Problem.** A learner paused at time T asks a question. An answer built from
what is said or shown *after* T spoils the video.

**Technique.** Retrieval keeps a passage only when `end_ms <= T`. A passage with
an unknown end is excluded, never included. The gate sits in the vector-store
query itself and is checked again in Python afterwards.

**Why it matters.** An early evaluation with a looser rule (`start_ms <= T`)
measured a 48% production-gap rate. A passage that starts before T can still
finish after it.

**Evidence.** The eval harness (25 golden questions, real Chroma store):
leakage rate 0.0, production-gap rate 0.0, refusal correctness 1.0, citation
accuracy 1.0. Retrieval accuracy is 15/16 with the offline hashing embedder
(no network); it has not been re-run with the Gemini embedder.

**Talking point.** Everything below (streaming, windows, speed-ups) was built
to leave this gate unchanged and to be safe against it.

## 2. Answers you can trust: validated citations and honest refusals

- Every answer must cite passages from the retrieved set. An answer with no
  citation, or one that cites an unknown id, is refused rather than passed on.
- Refusals are honest about *why*: no evidence yet, a scope problem, or
  "this part of the video hasn't been processed yet" (section 4).
- Two modalities are cited and labelled: `audio` (transcript) and `frame`
  (what was on screen).

## 3. Seeing, not just hearing: frame descriptions and live frames

- Every 4 s of video is sampled as a frame and described by a vision model, so
  questions like "what equation is on screen?" work.
- A frame's timestamp always comes from the sampler's index, never from the
  model's reply. A model that mislabels a frame cannot move it earlier in time.
- "Ask about this frame" captures the current frame in the browser and
  describes it at exactly T. It satisfies the gate by equality
  (`start_ms == end_ms == T`).

## 4. Streaming ingest: usable before it is finished

**Problem.** Processing an 11-minute video takes many minutes. Students should
not wait for all of it.

**Technique.** The video is processed in 2-minute windows. Each window is
transcribed, described, chunked and embedded, and it is queryable the moment
it is written. Two modes, chosen at upload:

| Mode | Behaviour |
|---|---|
| Process everything now (`full`) | One job walks all windows. A progress bar shows `processed_through_ms / duration_ms`. |
| Stream | The first 3 windows (6 min) are processed at upload. The video is attached to its subject after the first window. The player then reports the playback position, and the next window is queued when the student gets within one window of the processed point. |

**Safety design.**
- Each chunk carries `processed_through_ms` and `duration_ms`. The store
  reports a resource as `partial` until the two meet.
- A question past the processed point gets an honest "not processed yet"
  answer and no model call, and it is not allowed to fall back to an
  uncited general-knowledge answer.
- The marker lives in the vector store, not in memory. The position endpoint
  works out the next window from the store, so it still works after a server
  restart.
- Chunk ids are offset per window so windows never collide. Re-running a window
  deletes exactly that resource's chunks in that time range first, so no stale
  chunks are left.

**Measured.** In a live stream-mode run on the demo video, the job reported
`windows_done` / `windows_total` as it went, and `processed_through_ms` reached
360000 (6:00) after the third window with visual coverage 100%. Before the
speed-ups below, one window took roughly 3.5 minutes end to end.

## 4b. Transcript first, visuals after, overlapped and read ahead

**Problem.** Even with windows, each window waited for both the transcript and
the slow vision pass before anything was usable.

**Technique.** Each 2-minute window is split into two stages that are written
separately:

- **Stage T (transcript):** speech to text, chunk, embed, store. Fast, so the
  window is askable early.
- **Stage V (visuals):** frame descriptions, chunk, embed, store. Slower.

Three pieces make this work:

1. **Two frontiers.** `processed_through_ms` is the transcript frontier and
   `visual_through_ms` is the visual one. Resources that predate the visual
   marker are treated as complete, never as "visuals pending".
2. **Overlapped lanes.** One transcript worker and one vision worker run at the
   same time, so window N+1's transcript is produced while window N's frames are
   still being described. They use different resources (local Whisper and the
   Gemini API). Results are meant to be identical to running the stages one
   after another, and a failure in either lane fails the job instead of being
   hidden.
3. **Reading further ahead.** The position heartbeat now keeps up to two
   windows (4 minutes) of transcript ahead of the playhead, plus a catch-up job
   for visuals. The resource only reports `ready` when both frontiers reach the
   end of the video.

**Honesty rules.**
- If a question falls between the two frontiers, it is answered normally from
  the transcript. If that gives too little evidence, the refusal says the
  on-screen content is still being processed, and it does not fall back to
  general knowledge.
- Chunk ids for the two stages never collide, and a re-run can delete only one
  stage's chunks for a window by exact resource id and time range.
- The strict `end_ms <= T` gate is unchanged.

**Status.** Covered by unit tests with fake Whisper and Gemini clients (428
tests passing). **Not yet timed on the real stack**, so there is no speed-up
figure for it; do not quote one.

## 5. Cheaper embeddings: true batching sized to the quota

**Problem.** The embedding call returns *one pooled vector* for many texts, so
the original code fell back to one HTTP request per chunk: about 257 calls for
an 11-minute video.

**Technique.** One `Content` per text in a single call. The count returned must
equal the count sent, and the order is verified against the real model once
per instance, because the store pairs vectors with chunks by position.
A 429 retries the whole batch, not one text.

**Batch size is 64, not the API's limit of 100.** The quota is metered in
texts per minute (100). A 100-text call uses the entire allowance, so the order
check or a concurrent query pushes the next call over; a live 253-text run was
refused at 100. At 64 there is headroom.

**Measured.** Before: 257 sequential calls for 253 texts. At the earlier batch
size, 253 texts took 7 calls (measured). At 64 it is 4 calls (by construction).
A 97-chunk PDF embedded in about 20 s end to end in a live run.

## 6. Making the vision pass faster

One 120 s window, 30 frames, real Gemini calls, same video and same window.

| | Before | After |
|---|---|---|
| Total vision time | 114.9 s | 54.6 s (**2.1x faster**) |
| Frames sent to the model | 30 | 21 (9 skipped as repeats) |
| Average bytes per call | about 244 KB | about 98 KB |
| Calls | 4, one after another | 4, three at a time |

Where the time went: per-call model latency, not our pacing. The four calls
took 53, 20, 15 and 25 s; the 4 s pacing floor and retry waits were not the
cause. The speed-up is noisy because single Gemini calls ranged from 15 to
89 s across runs.

Three techniques:

1. **Skip near-duplicate frames.** A frame is skipped when it matches the last
   kept one. Frames are compared on a 64x64 grayscale grid of 8x8 cells, and a
   change in *any* single cell counts as a change, so a new line written on a
   board is not lost. The first frame of a window and at least every fifth
   frame are always kept. A kept frame's description covers the whole
   unchanged span, and its `end_ms` only moves later, which makes the gate
   stricter, never looser.
2. **Cheaper calls.** Frames are downscaled to at most 768 px and sent as
   JPEG at quality 80, and the prompt asks for 1-2 concise sentences per
   frame. Raw frame bytes dropped by about 59%.
3. **Bounded parallelism.** Three batches run at once. A global limiter keeps
   the spacing between call *starts*, so the free-tier request rate is still
   respected. A 429 on any worker pauses all of them.

**An honest tuning story.** The first duplicate threshold (4.0) was more
aggressive: it sent only 9 frames, but it lost the last 16 s of board changes.
Measuring the per-cell differences showed static frames at 0.14-0.39 and real
incremental changes at 1.7-5.2, so the default is 1.0. At that setting every
distinct on-screen state in the baseline still appears in a description.

**Limits.** The threshold was tuned on one lecture. A talking-head video with
no repeated frames would gain only from smaller frames and parallel calls.

Knobs (environment variables): `VISION_CONCURRENCY=3`, `VISUAL_DEDUPE=1`,
`VISUAL_DEDUPE_CELL_THRESHOLD=1.0`, `VISUAL_DEDUPE_FORCE_KEEP_EVERY=5`,
`VISUAL_FRAME_MAX_SIDE=768`, `VISUAL_SEND_JPEG_QUALITY=80`.

## 6b. Faster speech-to-text per window

**Problem.** Transcribing one 2-minute window took about 2 minutes in the app,
with the smallest Whisper model (`tiny`, int8, CPU).

**Cause.** Each window handed Whisper the whole video file plus a time range.
In that mode Whisper decodes the entire file, turns off its silence skipping,
and we also used the slower beam search (beam 5).

**Technique.** Decode only the window's audio (seek, then trim using the
decoded frames' own timestamps), transcribe those samples with greedy decoding
(beam 1) and silence skipping on, then add the window start back so every
segment carries its real video time. The decoded window was checked against a
full-file decode: 0 ms offset on three windows. Segments that run past the
window end are still dropped, so the timestamp gate sees the same times as before.

**Measured** (same 2-minute window, same machine, other apps running):

| | Old call | New call |
|---|---|---|
| Whisper `tiny`, one 120 s window | 28.0-30.4 s | 13.6-17.5 s (about 2x) |

On a quieter machine, a first benchmark measured 27.9 s for the old call and 2.5 s
for the new one. That benchmark did not check what else was using the CPU, so quote
the 2x figure. Inside the app the vision lane shares the same CPU, so in-app
times are higher; an in-app re-time has not been done yet.

## 7. Honest degradation: stated, never inferred

Several bugs came from the system quietly doing less than it claimed:

- Visual coverage was silently capped at 24% by a hard frame limit. Now every
  ingest reports `visual_status` (complete / partial / failed / skipped) and
  `visual_coverage_pct`.
- A scanned PDF with no text layer must never look like a document that was
  read and happened to be empty. It reports `skipped` or `failed` with the real
  reason.
- A resource id that merely looks valid but matches no indexed chunk silently
  refuses every question, so scope ids are verified against real chunks.

## 8. Managing resources safely

- `DELETE /courses/{course_id}/resources/{resource_id}` requires the course to
  own the resource, detaches it from every subject, then removes its chunks and
  staged file. Deletes match on exact resource id, never a prefix.
- After a delete, the learner's stored session grant is refreshed so a stale
  grant cannot lock them out.

## 9. Fitting into an LMS (see also ADR-001, ADR-002)

- The tutor is a separate service launched from the LMS with a signed launch
  that carries the learner, course and allowed resources.
- Today it runs as a single process with in-memory sessions, jobs and subjects
  and a local vector store. Scaling to many concurrent users needs: stateless
  sessions, Postgres and pgvector, object storage for media, ingest workers
  separate from the chat API, a shared Gemini rate limiter and role-based
  authorization. Concurrent chat load has **not** been measured.

---

## Numbers to remember

| Claim | Number | Basis |
|---|---|---|
| Spoilers served in eval | 0 of 25 questions | measured, Chroma store |
| Embedding calls for a 253-text video | 257 before, 4 now | 257 and 7 measured; 4 by construction |
| Vision time for one 2-minute window | 114.9 s down to 54.6 s | measured, one run each, noisy |
| Frame bytes per call | down about 59% | measured |
| Time to first usable answers (stream) | first 6 minutes of video | measured on demo video |
| Transcript-first / overlapped lanes speed-up | not measured yet | tests only, fakes |
| Whisper time for one 2-minute window | about 29 s down to about 15 s | measured, two runs each, loaded machine |
| Tests | 428 passing | measured |
