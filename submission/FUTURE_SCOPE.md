# Future scope

Known gaps in the current prototype, and what fixing each one would involve.
None of them affects the safety guarantees (the strict timestamp gate and
citation validation).

## 1. Deleting a resource while it is still being processed

**What happens today.** `DELETE /courses/{course_id}/resources/{resource_id}`
removes the resource from every subject, deletes all of its chunks and vectors
(Chroma locally, Pinecone when hosted), and deletes the uploaded file. It does
not stop ingest jobs that are still running for that resource. A video is
processed in 2-minute windows on a worker thread. If one of those windows
finishes after the delete, it writes that window's chunks back into the index.

**Effect.** Orphaned chunks for a resource that no longer exists. No subject
lists that resource any more, and chat only searches resources in the
learner's granted scope, so learners never see these chunks. They do take up
space in the index, and they cost embedding quota to create.

**Not tested yet.** This comes from reading the code
(`apps/api/app/ingestion/router.py`, `delete_resource`; `jobs.py`,
`ingest_media_windows`). There is no test that reproduces it.

**How to fix it.**
1. Keep a set of deleted (tombstoned) resource IDs. The delete route adds the ID
   before it purges.
2. Before each window commits its chunks (`_commit_stage` in `jobs.py`), check
   the set and stop if the resource has been deleted. Also stop the watch-ahead
   windows queued by `report_position`, which the router tracks in `_INFLIGHT`.
3. Run the purge once more after the last running job for the resource ends,
   to catch any window that committed in the meantime.
4. Add a test: start a windowed ingest with a slow stub transcriber, delete the
   resource partway through, and check that no chunks remain once the job ends.

Until then, wait for processing to finish before deleting a video.

## 2. Near-duplicate screen descriptions crowd search results

**Found by the evaluation on our own course** (see
[EVALUATION.md](EVALUATION.md#a-evaluation-on-our-own-course-material)). The
screen is described every 4 seconds. While the board does not change,
consecutive descriptions are almost word for word the same, so a search can
return six copies of one board description and push out the spoken sentence
that actually answers the question. This accounts for all 4 retrieval misses
and two of the three answer misses in that evaluation, and for why taking the
top 8 results finds nothing the top 5 did not. (The third answer miss, `pd-v8`,
refused even though search found its answer; that is not yet investigated.)

**How to fix it.**
1. At ingest, merge consecutive screen descriptions that are near-identical into
   one piece spanning their combined time (its end time stays the latest frame's,
   so the spoiler guard is unchanged).
2. At query time, drop a result whose text is near-identical to one already
   ranked higher, and fill the slot with the next distinct piece.
3. Re-run `python -m app.eval.end_to_end` and compare against
   `partial-derivatives-end-to-end.json`.

## 3. Cached answers that can never be used

Gemini answers are cached on disk, keyed by a hash of the full prompt, and the
prompt includes the retrieved passages. Deleting a resource does not delete
cached answers built from its passages. Those entries can never be used again,
because those passages can't be retrieved any more, but they stay on disk.
**Fix:** store the IDs of the resources each cached answer drew on, and remove
those entries when the resource is purged. Or give cache entries an expiry
time.

## 4. Developer view in production

The live pipeline trace (`GET /dev/trace`, the `/dev` page) is meant for
demos:

- **Anyone can read it.** Any visitor can see recent questions from every user.
  **Fix:** require an admin token, or show each session only its own runs.
  Until then, `DEV_TRACE=false` turns recording off.
- **It's lost on restart.** It lives in the API process's memory, so a restart
  clears it, and with several API workers each would keep its own trace.
  **Fix:** publish the events to a shared store (for example Redis streams)
  and have the page subscribe to them, instead of polling a buffer in memory.
