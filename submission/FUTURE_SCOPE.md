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

## 5. Transcript tab

The lesson panel has a Transcript tab, but it is UI only: no route serves a
resource's transcript yet. **Fix:** a `GET /resources/{id}/transcript` that
reads the transcript chunks by exact `resource_id`, in time order, and passes
them through the same strict `end_ms <= T` gate as chat, so the tab never
shows speech the learner has not reached.

## 6. Chapters of uploaded files are lost on restart

Chapters are stored as one JSON file per resource on the API's disk
(`data/chapters/`). On Render's free tier that disk is wiped on every restart,
so chapters of files uploaded on the hosted site are rebuilt, at the cost of
one model call each, the first time the resource is opened afterwards. Only the
demo video's and PDF's chapters are baked into the image. **Fix:** keep the
chapter records in Supabase (a table, or a JSON object in Storage next to the
media file), which now holds the rest of the persistent state.

## 7. Supabase writes fail quietly

Writes to Postgres are best effort: a failure is logged as a warning and the
request still succeeds, so a learner's chat is never lost to a database
hiccup. The cost is that a misconfigured project, for example one missing the
`0003_grants.sql` privileges, looks healthy while saving nothing. **Fix:** a
`GET /health/supabase` that writes and deletes a probe row with the service
key, and a counter of failed writes in the Developer view.

## 8. Chat images after a reload

An attached image stays in the chat's context after a reload (its transcript
is stored), and the file is kept in Storage, but the chat does not show the
thumbnail again: the user bubble only names the file. **Fix:** list a chat's
attachments with short-lived signed Storage URLs and render them in the
message they were sent with.

## 9. Old chats whose session is gone

The Malai sidebar keeps a local list of chats in the browser. A chat whose
server session no longer exists (from before Supabase persistence, or after a
wipe with `supabase/maintenance/clear_chats.sql`) still shows in that list and
opens empty. **Fix:** drop list entries whose session answers `404` on the
first history load.

## 10. Voice sessions on one small instance

Each open voice session holds a Gemini Live connection relayed through the
API, for up to 10 minutes. On the free 512 MB Render instance this limits how
many learners can talk at once (not yet measured). **Fix:** measure it, then
cap concurrent voice sessions with a readable "voice is busy" close, or
issue short-lived Gemini Live tokens so browsers connect directly while the
server still answers every course question through `ask_course`.
