# To-do

Open engineering tasks, in priority order. Added 2026-10-21. None started.

## Next

1. **Zero-chunk window re-queues forever.** A window that yields no chunks
   (silence, no on-screen change) writes no progress marker, so the `/position`
   heartbeat queues it again every ~10 s and spends Groq and Gemini requests each
   time. Write the frontier marker even for an empty window, without adding a
   searchable chunk. Small.
2. **Timed real stream-mode run.** Transcript-first, lane overlap, Groq and the
   window-only Whisper call are all unit-tested but never timed end to end in
   the app. Measure time to first answerable question and per-window time for
   both stages, then record them in `docs/TECHNIQUES.md` (replace the "not
   measured yet" row). This decides whether a smaller first window is worth it.

## Then

3. **Lighter vision pass.** Now the slowest stage (~55 s per 2-minute window)
   and a Gemini quota user. Try one frame every 8 s instead of 4, and test
   Flash-Lite for frame descriptions; measure coverage before/after.
4. **Fewer embedding requests.** The free tier's 1,000 embed requests/day was
   hit once. Ideas: cache repeated query embeddings; merge a window's transcript
   and visual embeds into one call where the stages line up. A paid key is the
   real fix for a demo with many users.
   - Alternative: switch to `nvidia/nemotron-3-embed-1b:free` via OpenRouter.
     It has been probed and returns 2048-d vectors, against Gemini's 3072-d.
   - That means a new Pinecone index and re-ingesting every resource.
   - Query and chunk vectors must come from the same model, so Gemini can only
     be a fallback if each chunk is embedded into both indexes.
5. **Smaller first window** (about 30 s, then 120 s windows) for a faster first
   answer. Chunk ids assume 120 s windows, so the ordinal scheme must change.
   Decide after task 2.

## Later

6. **Visual-stage retry** with an honest "failed" state when the vision lane
   fails on a stream resource (today it stays visual-pending until a later
   `/position` re-queues it).
7. **Auth on state-changing routes:** upload, delete, subjects, position.
8. **Scaling plan Phase A** (`docs/SCALING_PLAN.md`): stateless sessions,
   subjects and jobs out of process memory.
9. **Re-run the eval with the Gemini embedder** (15/16 retrieval accuracy was
   measured with the offline hashing embedder) and review `DEFAULT_THRESHOLD=0.08`.

## On hold

- **BYOK (bring your own key).** Undecided who supplies keys (teacher or
  student); there is no teacher role yet, every user is a student.
