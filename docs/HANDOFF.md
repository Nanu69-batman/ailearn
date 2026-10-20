# Handoff — AI Learning Assistant (Malai Pioneer)

Last updated: 2026-10-15. Read this before touching the repo.
Binding operating rules live in `AGENTS.md` at the repo root.

## 1. Verified current state

| Check | Result |
|---|---|
| Backend tests | **319 pytest pass** |
| ruff | clean |
| Web `tsc --noEmit` | clean |
| Web `npm run build` | pass |
| Web `npm run contract-check` | **PASS — 77 assertions** |
| Stack | FastAPI + Chroma + LangChain splitters, Next.js 15 + Tailwind v3 |

Ports: API `8123`, web `3000`. Start both with `run.bat` at the repo root.
**Use this interpreter:** `C:\Users\Nimish-2\AppData\Local\Programs\Python\Python314\python.exe`
— plain `python` on PATH is 3.13 and has no pytest or uvicorn.

The working tree carries uncommitted changes in `apps/api/app/ingestion/embedder.py`,
`apps/api/tests/test_embedder_batching.py` and this file. `run.bat` selects the interpreter by probing
candidates and keeping the first that passes `import uvicorn, chromadb`, so
it does not depend on that path being right on another machine.

## 2. Commits landed since the previous handoff

| Commit | What it changed |
|---|---|
| `6142ba3` | Grounded vs supplemental answering (`mode` / `grounded` on the response, supplemental path in `recency.py` + `chat/service.py`), TA register and anti-spoiler suppression in `prompts.py`; added `PROJECT.md` and `tests/test_ta_behaviour.py`. **Restored to the committed+pushed state today; the earlier working-tree removal is preserved at `.git/supplemental-removal.patch`, and the suite is green again (319 passed).** |
| `712d227` | `claims.md` defence sheet (judging-criteria answers) plus a `PROJECT.md` expansion with Mermaid diagrams and corrected figures. `claims.md` is deleted in the working tree, so it lives in history only. |
| `ab2406f` | `PPT_CONTENT.md` — deck copy mapped to the 6-slide BuildVerse template. Also deleted in the working tree today. |
| `01f42a8` | Slide 3 converted to a numbered flow; two cramped diagrams dropped. Deck copy only. |

## 3. Demo resource (as ingested)

| Item | Value |
|---|---|
| resource_id | `res_2e629d682a3d` |
| title / duration | `Partial-derivatives.mp4`, 655.5 s |
| course_id / subject_id | `demo` / `sub_06dea46b30bf4ee7` |
| Transcript chunks | 89 (`0..656380 ms`) |
| Visual chunks | 174, from **164 sampled frames** (`0..652000 ms`) |
| Visual coverage | **100%** |

Dev probe data is also in the store and on disk: `r1` (89 chunks), `res_probe` (121),
plus throwaway courses `coverageprobe`, `titleprobe`, `imageprobe` (one test PNG).
**Leave all of these alone** — recorded so a future agent does not "clean them up".

## 4. Routing

| Route | Page |
|---|---|
| `/` | landing, one button to `/dashboard` |
| `/dashboard` | subjects overview |
| `/ai-tutor/subjects` | AI Tutor nav target |
| `/ai-tutor/subjects/[subjectId]` | subject page — YouTube form + file upload |
| `/ai-tutor/subjects/[subjectId]/content/[resourceId]` | player + chat |

Legacy `/subject/[courseId]...` and `/demo` redirect — see `apps/web/next.config.mjs`.
There is no `/demo` page.

## 5. Features live now

1. **Recency-aware retrieval** (`app/retrieval/recency.py`). `is_recency_seeking()`
   classifies present-tense questions; `store.query(prefer_recent=True)` over-fetches
   (60 vs 20) and reorders an **already gated** pool. Attribute questions (colour,
   shape, label) are deliberately not recency-seeking — a colour does not change.
2. **Citation modality.** Citations carry `modality` (`"transcript"` | `"visual"`) from
   the chunk's stored `kind`; an unknown kind maps to `None`, never defaulted to
   `"transcript"`. The UI badge shows `frame` / `audio`.
3. **Live frame capture** (`app/ingestion/liveframe.py`). Client-side canvas capture; the
   frame becomes a passage with `start_ms == end_ms == T`, so it satisfies `end_ms <= T`
   by equality and needs **no special-casing in the gate**. Triggered by "Ask about this
   frame" or `is_frame_question()` phrasing. The server validates T against a
   container-header duration (the request carries no duration field); out-of-range T
   returns 422, never clamped. Provenance lives only in `metadata["live"]`, never in
   Chroma. Documents make no vision call and degrade honestly.
4. **LaTeX rendering** (`components/MarkdownBody.tsx`): react-markdown + remark-math +
   rehype-katex. **No** `rehype-raw` (keeps LLM HTML inert), KaTeX `trust: false`;
   contract-check walks `app/`, `components/`, `lib/` for `rehype-raw` imports. Gotcha:
   `remarkMath({singleDollarTextMath:false})` is **not** the display-math fix — it
   disables inline `$...$` entirely.
5. **File upload on the subject page** (`components/FileIngestForm.tsx`):
   video/pdf/pptx/png/jpg/jpeg/webp. `POST /courses/{course_id}/ingest` takes an
   optional multipart `subject_id`, mirroring the YouTube route.
6. **Image ingest** (`app/ingestion/images.py`): `source_type == "image"`, deliberately
   **not** in `TIME_GATED_SOURCE_TYPES` — an image has no timeline and is fully visible
   whenever looked at, like a PDF page. Gating it would make it permanently invisible: a
   null `end_ms` is the EXCLUDE sentinel. Image citations carry `image_no`, not a time.
7. **Resource purge** (`app/ingestion/purge.py`) removes chunks **and** the staged asset.
   `stream_media`, `list_resources` and `purge_resource` share one `resolve_asset()`, so
   purge cannot resolve a different file than the one served.
8. **Session-grant recovery.** `LearnSessionProvider` persists
   `ailearn.session_resource_ids` and re-mints when it no longer matches. Before this the
   grant was written once and `sessionStorage` outlived every reload, so a re-ingest
   locked the learner out permanently.
9. **`scope_empty`** on `/chat` separates "grant does not cover this resource" from
   ordinary low-evidence refusal, so the UI stops blaming the learner for a stale grant.
10. **OCR for scanned documents** (`app/ingestion/ocr.py`). Heavy imports (`torch`,
    `transformers`) are confined to one seam, `_unlimited_ocr_multi`, and availability is
    probed with `importlib.util.find_spec` — so the app boots with none of them
    installed. A PDF whose text layer is empty is OCR'd, and if OCR cannot run the
    report says `skipped`/`failed` with the real reason. A scanned PDF must never come
    back looking like a document that was read successfully and happened to be empty.
    Off by default; enable with `OCR_BACKEND=unlimited-ocr`.
11. **True batched embedding** (`app/ingestion/embedder.py`). `batchEmbedContents`
    returns 1 pooled vector for N texts, so the old path fell back to one HTTP request
    per chunk — ~257 sequential calls for an 11-minute video, which is why ingest took
    minutes. `embedContent` now sends one `Content` per text in a batch of up to 64 (`BATCH_SIZE`; the API hard limit is 100, but the quota is metered
    in *texts*/min, so a 100-text call spends the whole allowance and the order self-check
    or a concurrent query tips the next call over — 64 leaves headroom).
    **253 texts -> 4 calls of <=64, vs 257 sequential before.** The quota is
    100 *texts*/min, so batching cannot lift the ~2.5-minute floor on its own —
    what it removes is the request count and the 429s that came with it. A local
    token bucket (`_TextQuota`) paces the calls so the ingest spends the quota's
    own time instead of colliding with it. Order is
    load-bearing — the store zips vectors against chunks positionally — so the returned
    count is asserted against the count sent, and `_verify_order_once` proves the
    order against the real model once per instance. `tests/test_embedder_batching.py`
    covers the batching, the alignment guards, the pooling fallback, the quota pacing
    and the backoff.
- **Progressive ingest.** `POST /courses/{id}/ingest` takes `mode=full|stream` (default `full`; 422 otherwise; video files only, YouTube unchanged). Windows are 120 s; `stream` does the first 3 then stops partial; `POST /courses/{id}/resources/{rid}/position {t_ms}` queues the next window when `t_ms + 120 s >= frontier` (statuses queued/running/idle/ready; 409 if the media file is gone). Job payload adds `mode, processed_through_ms, duration_ms, windows_done, windows_total`. A window yielding zero chunks cannot advance the frontier. Tests: `tests/test_progressive_ingest.py`.

### Staged ingest (transcript first, visuals after)

- Each 2-min window is two independently committed stages: T (transcribe -> transcript chunks) then V (frame description -> visual chunks). `processed_through_ms` = transcript frontier; `visual_through_ms` is stamped on visual chunks (and on transcript chunks: window end if no visual pass, else 0). No marker = legacy = never visual-pending.
- One transcript lane + one vision lane overlap (`ingest_media_windows(..., pipelined=True)`); output identical to serial. Either lane failing fails the job. Job progress adds `visual_through_ms`, `transcript_windows_done`, `visual_windows_done`.
- Chunk ordinals per window: transcript `[off, off+60000)`, visual `[off+60000, off+120000)`. `store.delete_resource_range(..., kind="transcript"|"visual")`.
- Chat: `ChatResponse.visual_pending` / `visual_through_ms`; low-evidence refusal while pending uses `VISUAL_PENDING_REFUSAL_TEXT` and skips the supplemental fallback.
- `POST .../position` queues up to 2 stage-T windows (lookahead 240 s) plus a V catch-up job; returns `job_ids` (+ `job_id`), `visual_through_ms`; `ready` only when both frontiers are complete; in-flight guard is in memory. Stream upload attaches to the subject after first stage T.
- Tests: `tests/test_staged_ingest.py`.

## 6. Bugs found and fixed — do not reintroduce

- **Visual coverage was silently capped at 24%.** `ingest_media(...,
  max_visual_frames=40)` fed a silent `break`; 40 frames x 4 s covers only `0..156 s` of
  a 655 s video, plus a blanket `except Exception: log.warning` around the whole visual
  pass. Now a `VisualPassReport` (complete/partial/failed/skipped) surfaces
  `visual_status`, `visual_coverage_pct` and `warnings` — degradation is **stated**, never
  inferred from a count.
- **Frame timestamps came from the MODEL's JSON.** `visual.py` took `int(item["index"])`
  from the model reply. A frame sampled at 650 s labelled `index: 5` would store at
  `start_ms=20000` — a spoiler leak. Indices are validated against the sampler's real
  indices at two levels; unknown ones are dropped and recorded, never clamped or
  reassigned.
- **Visual segments were time-split by character proportion.** Correct for speech, wrong
  for a frame (an instant). It produced 135 ms citations reading "with respect to $y$: "
  — the trailing 3.4% of the parent description cut mid-sentence.
- **Shared retry/backoff** (`app/ingestion/backoff.py`): a 429 must retry the whole
  batch, not one text — a per-text 2s base backoff burns all its attempts inside a
  single quota window.
- **Cross-origin canvas**: the page is `:3000` and the media is `:8123`, so `toBlob` needs
  `crossOrigin="anonymous"` on the `<video>` element. Without it live-frame capture fails
  *silently* while every test passes.
- **A green suite can hide a red tree.** The working tree once removed the supplemental
  answering feature from `recency.py` and `app/chat/*` without updating
  `tests/test_ta_behaviour.py`, which then failed to import — a *collection* error, so
  19 tests never ran while the visible count looked plausible. Check that the suite
  actually collects, not just that it passes.

## 7. Live verified probe table — `res_2e629d682a3d`

| T (ms) | question | result | cited | modality |
|---|---|---|---|---|
| 2000 | What is written on the board? | REFUSED (strict-T correct) | - | - |
| 30000 | What equation is on screen? | answered | 28000 | visual |
| 300000 | what is a partial derivative? | answered | 23000, 284010 | transcript |
| 480000 | live frame ("Ask about this frame") | answered, `live: true` | 480000 | visual |
| 600000 | What colour is the curve? | REFUSED — curve is early in the video, honest refusal | - | - |

Every citation satisfies `end_ms <= T`. No gate breaches.

## 8. Safety invariants — must not be weakened

1. **Strict timestamp gate.** Keep a passage only when `end_ms <= T`. Never relax to
   `start_ms`. A NULL `end_ms` **excludes**. An earlier eval measured a 48%
   production-gap rate with a loose gate.
2. **Citations are validated** against the retrieved set. Uncited or unknown-ID answers
   are refused, not passed through.
3. **Scope is intersected, not replaced.** `focus_resource_id` must be intersected with
   the granted `resource_scope`. Replacing it was a real past bug.
4. **Plausible-looking ids are not real ids.** A scope of `["res_video", ...]` matches no
   indexed chunk and silently refuses every query. Verify scope ids are real chunk ids.
5. **Secrets.** `apps/api/.env` holds Gemini keys and the launch secret. Never commit,
   echo, or read it into a brief.
6. `ALLOW_UNSIGNED_LAUNCH=true` and `LMS_LAUNCH_SECRET=dev-secret` are localhost-demo
   only. Production requires real HMAC-signed launches.

**Green tests have repeatedly hidden real bugs in this repo.** A missing React import
passed `tsc`; a ChatPanel prefix bug shipped past a green suite; a cleanup agent
reported "exactly one resource" when there were two; the visual coverage cap sat behind
a fully green suite. **Live probes are mandatory before claiming success.**

## 9. Gotchas

1. Use the Python 3.14 executable from section 1; PATH `python` is 3.13 with no
   pytest/uvicorn.
2. **Never run `npm run build` while `npm run dev` runs** — shared `.next` causes
   `Cannot find module './571.js'`, `__webpack_modules__[moduleId] is not a function`,
   500s. Recovery: kill node, delete `apps/web/.next`, restart.
3. `*.tsbuildinfo` and `*.log` are gitignored — do not commit them.
4. `Set-Content` on a tracked source file truncates it. Append, or `git checkout --`.
5. Chunk deletes must be scoped by **exact `resource_id` equality** — never by course,
   prefix, or time range.
6. Gemini free tier: 100 embedding *texts*/min, 15 req/min. `429 RESOURCE_EXHAUSTED` is
   expected under load. Wait ~90 s, retry once, never retry-loop.
7. Multiple agents killing uvicorn on 8123 mid-ingest corrupt a job. Sequence restarts.
8. `POST /lms/session` requires `learner_id`, `course_id`, `resource_id`, `resource_scope`,
   `current_time_ms`, `issued_at` (epoch **seconds**). `POST /chat` requires
   `session_token` in the body **and** the `X-Session-Token` header; getting it wrong
   gives 401/422 that look like app bugs.
9. `ChatPanel` must not prefix every refusal with "I can't answer from watched content
   yet" — it contradicted the honest scope message.

## 10. Servers

```powershell
# API - always start from apps/api (env file resolves relative to the package)
cd apps/api
& "C:\Users\Nimish-2\AppData\Local\Programs\Python\Python314\python.exe" `
  -m uvicorn app.main:app --port 8123

# Web
cd apps/web; npm run dev -- --port 3000
```

`apps/web/.env.local` — `NEXT_PUBLIC_API_URL=http://localhost:8123`.
`apps/api/.env` — gitignored; never commit or echo its values.

## 11. Known gaps — honest, not papered over

- **No delete-resource endpoint or UI.** `purge_resource` exists but has no caller, and
  it does **not** detach from subjects. Exposing it needs a course-ownership check
  *and* subject detach first.
- **Ingest is faster but still paced by the free tier.** Embedding is now genuinely
  batched (4 calls of <=64 instead of 257 for an 11-minute video), but the Gemini free
  tier allows 100 embed *texts*/min and ~15 vision requests/min, so the 164-frame
  description pass is deliberately serialised with a 4s floor between calls. That
  pacing is the dominant cost of a fresh ingest now, and it is quota, not code. A paid
  tier removes it.
- **The eval harness runs again** (`python -m app.eval.harness`, from `apps/api`),
  against a temporary `ChromaVectorStore` + offline `HashingEmbedder`, and reports
  `gap_mode: chroma`. Current results: `docs/eval-results/chroma-2026-10-20.json`
  (leakage 0, production gap 0). `baseline*.json` / `smoke*.json` there are
  pre-rebuild and not comparable. `DEFAULT_THRESHOLD=0.08` is non-binding on Chroma
  cosine scores and still untuned.
- **YouTube ingest cannot be verified in this environment** (captions/download
  unavailable).
- **Sessions are in-memory.** Postgres + pgvector is the planned migration — see
  `docs/adr/ADR-002-retrieval-stack-library-migration.md`.
- **Only one 11-minute video has been exercised end to end.** No long-form,
  multi-page-doc, or second-subject coverage.
- **Dev probe data is still present** in the store and on disk: `r1`, `res_probe`,
  `coverageprobe`, `titleprobe`, `imageprobe`.

## 12. Docs map — what is authoritative

| Doc | Status |
|---|---|
| `PROJECT.md` (repo root) | current architecture, feature inventory, decision log, roadmap |
| `docs/HANDOFF.md` | this file — verified state, probes, gotchas |
| `docs/TODO.md` | open engineering tasks, prioritised |
| `docs/TECHNIQUES.md` | presentation notes with measured numbers |
| `docs/SCALING_PLAN.md` | plan for LMS module + concurrent users (not implemented) |
| `docs/adr/ADR-001-lms-integration-scalability.md` | LMS boundary + scaling decision (history) |
| `docs/adr/ADR-002-retrieval-stack-library-migration.md` | library / persistence migration decision (history) |
| `docs/api.md` | **stale** — old port and route list; the HMAC payload spec is still right |
| `docs/deployment-guide.md` | target topology (Postgres / Neon / R2), not the current runtime |
| `docs/research-*.md` | design input for the ADRs; not a description of the shipped system |
| `docs/eval/*`, `docs/eval-results/*` | eval assets and the scoreboard of the pre-rebuild harness |
