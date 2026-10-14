# Handoff — AI Learning Assistant (Malai Pioneer)

Last updated: 2026-10-14. Read this before touching the repo.
Binding operating rules live in `AGENTS.md` at the repo root.

## 1. Verified current state

| Check | Result |
|---|---|
| Backend tests | **276 pytest pass** |
| ruff | clean |
| Web `tsc --noEmit` | clean |
| Web `npm run build` | pass |
| Web `npm run contract-check` | **PASS — 77 assertions** |
| HEAD | `cdb9520` |
| Stack | FastAPI + Chroma, Next.js 15 + Tailwind v3 |

Ports: API `8123`, web `3000`.

**Use this interpreter:** `C:\Users\Nimish-2\AppData\Local\Programs\Python\Python314\python.exe`
Plain `python` on PATH is 3.13 and has no pytest or uvicorn.

### Demo resource (as ingested)

| Item | Value |
|---|---|
| resource_id | `res_2e629d682a3d` |
| title / duration | `Partial-derivatives.mp4`, 655.5 s |
| course_id / subject_id | `demo` / `sub_06dea46b30bf4ee7` |
| Transcript chunks | 89 (`0..656380 ms`) |
| Visual chunks | 174, from **164 sampled frames** (`0..652000 ms`) |
| Visual coverage | **100%** |

Dev probe data is also still in the store and on disk: `r1` (89 chunks),
`res_probe` (121), plus throwaway courses `coverageprobe`, `titleprobe`,
`imageprobe` (one test PNG). **Leave all of these alone** — recorded here so a
future agent does not "clean them up".

## 2. Routing

| Route | Page |
|---|---|
| `/` | landing, one button to `/dashboard` |
| `/dashboard` | subjects overview |
| `/ai-tutor/subjects` | AI Tutor nav target |
| `/ai-tutor/subjects/[subjectId]` | subject page — YouTube form + file upload |
| `/ai-tutor/subjects/[subjectId]/content/[resourceId]` | player + chat |

Legacy `/subject/[courseId]...` and `/demo` redirect — see
`apps/web/next.config.mjs`. There is no `/demo` page.

## 3. Features added since the previous handoff

1. **Recency-aware retrieval** (`apps/api/app/retrieval/recency.py`).
   `is_recency_seeking()` classifies present-tense questions;
   `store.query(prefer_recent=True)` over-fetches (60 vs 20) and reorders an
   **already gated** pool. Attribute questions (colour/shape/label) are
   deliberately *not* recency-seeking — a colour does not change over time.
2. **Citation modality.** Citations carry `modality` (`"transcript"` |
   `"visual"`) from the chunk's stored `kind`. An unknown kind maps to `None`,
   never defaulted to `"transcript"`. UI badge shows `frame` / `audio`.
3. **Live frame capture** (`apps/api/app/ingestion/liveframe.py`). Client-side
   canvas capture; the frame becomes a passage with `start_ms == end_ms == T`,
   so it satisfies `end_ms <= T` with equality and needs **no special-casing in
   the gate**. Triggered by "Ask about this frame" or `is_frame_question()`
   phrasing. The server validates T against a container-header duration — the
   request carries no duration field. Out-of-range T returns 422, never
   clamped. Provenance lives only in `metadata["live"]`, never in Chroma.
   Documents (pdf/pptx/image) make no vision call and degrade honestly.
4. **LaTeX rendering** (`apps/web/components/MarkdownBody.tsx`):
   react-markdown + remark-math + rehype-katex. **No** `rehype-raw` (keeps LLM
   HTML inert) and KaTeX `trust: false`. contract-check walks `app/`,
   `components/`, `lib/` for `rehype-raw` imports. Gotcha:
   `remarkMath({singleDollarTextMath:false})` is **not** the display-math fix —
   it disables inline `$...$` entirely.
5. **File upload on the subject page** (`apps/web/components/FileIngestForm.tsx`):
   video/pdf/pptx/png/jpg/jpeg/webp. `POST /courses/{course_id}/ingest` takes
   an optional multipart `subject_id`, mirroring the YouTube route.
6. **Image ingest** (`apps/api/app/ingestion/images.py`): `source_type ==
   "image"`, deliberately **not** in `TIME_GATED_SOURCE_TYPES` — an image has
   no timeline and is fully visible whenever looked at, like a PDF page. Gating
   it would make it permanently invisible, because a null `end_ms` is the
   EXCLUDE sentinel. Image citations carry `image_no`, never a timestamp.
7. **Resource purge** (`apps/api/app/ingestion/purge.py`) removes chunks **and**
   the staged asset. `stream_media`, `list_resources` and `purge_resource`
   share one `resolve_asset()`, so purge cannot resolve a different file than
   the one served.
8. **Session-grant recovery.** `LearnSessionProvider` persists
   `ailearn.session_resource_ids` and re-mints when it no longer matches.
   Before this the grant was written once and `sessionStorage` outlived every
   reload, so a re-ingest locked the learner out permanently.
9. **`scope_empty`** on `/chat` separates "grant does not cover this resource"
   from ordinary low-evidence refusal, so the UI stops blaming the learner for
   a stale grant.

## 4. Bugs found and fixed — do not reintroduce

- **Visual coverage was silently capped at 24%.** `ingest_media(...,
  max_visual_frames=40)` fed a silent `break`; 40 frames x 4 s covers only
  `0..156 s` of a 655 s video, plus a blanket `except Exception:
  log.warning` around the whole visual pass. Now a `VisualPassReport`
  (complete/partial/failed/skipped) surfaces `visual_status`,
  `visual_coverage_pct` and `warnings`. Degradation is **stated**, never
  inferred from a count.
- **Frame timestamps came from the MODEL's JSON.** `visual.py` took
  `int(item["index"])` from the model reply. A frame sampled at 650 s labelled
  `index: 5` would store at `start_ms=20000` — a spoiler leak. Now validated at
  two levels against the sampler's actual indices; unknown indices are dropped
  and recorded, never clamped or reassigned.
- **Visual segments were time-split by character proportion.** Correct for
  speech, wrong for a frame (an instant). It produced 135 ms citations reading
  "with respect to $y$: ..." — the trailing 3.4% of the parent description cut
  mid-sentence.
- **Shared retry/backoff** (`apps/api/app/ingestion/backoff.py`):
  `batchEmbedContents` returns 1 pooled vector for N texts, so embedding falls
  back to one request per chunk (~265 requests against a 100/min cap).
- **Cross-origin canvas**: the page is `:3000` and the media is `:8123`, so
  `toBlob` needs `crossOrigin="anonymous"` on the `<video>` element. Without it
  live-frame capture fails *silently* while every test passes.

## 5. Live verified probe table — `res_2e629d682a3d`

| T (ms) | question | result | cited | modality |
|---|---|---|---|---|
| 2000 | What is written on the board? | REFUSED (strict-T correct) | - | - |
| 30000 | What equation is on screen? | answered | 28000 | visual |
| 600000 | What colour is the curve? | REFUSED — curve is early in the video, honest refusal | - | - |
| 300000 | what is a partial derivative? | answered | 23000, 284010 | transcript |
| 480000 | live frame ("Ask about this frame") | answered, `live: true` | 48000 | visual |

Every citation satisfies `end_ms <= T`. No gate breaches.

## 6. Safety invariants — must not be weakened

1. **Strict timestamp gate.** Keep a passage only when `end_ms <= T`. Never
   relax to `start_ms`. A NULL `end_ms` **excludes**. An earlier eval measured
   a 48% production-gap rate with a loose gate.
2. **Citations are validated** against the retrieved set. Uncited or
   unknown-ID answers are refused, not passed through.
3. **Scope is intersected, not replaced.** `focus_resource_id` must be
   intersected with the granted `resource_scope`. Replacing it was a real past
   bug.
4. **Plausible-looking ids are not real ids.** A scope of `["res_video", ...]`
   matches no indexed chunk and silently refuses every query. Verify scope ids
   correspond to actually-indexed chunks.
5. **Secrets.** `apps/api/.env` holds Gemini keys and the launch secret. Never
   commit, echo, or read it into a brief.
6. `ALLOW_UNSIGNED_LAUNCH=true` and `LMS_LAUNCH_SECRET=dev-secret` are
   localhost-demo only. Production requires real HMAC-signed launches.

**Green tests have repeatedly hidden real bugs in this repo.** A missing React
import passed `tsc`; a ChatPanel prefix bug shipped past a green suite; a
cleanup agent reported "exactly one resource" when there were two; the visual
coverage cap above sat behind a fully green suite. **Live probes are mandatory
before claiming success.**

## 7. Gotchas

1. Use the Python 3.14 executable from section 1. PATH `python` is 3.13 with no
   pytest/uvicorn.
2. **Never run `npm run build` while `npm run dev` is running** — shared
   `.next`, causes `Cannot find module './571.js'`,
   `__webpack_modules__[moduleId] is not a function`, 500s. Recovery: kill
   node, delete `apps/web/.next`, restart.
3. `*.tsbuildinfo` and `*.log` are gitignored — do not commit them.
4. `Set-Content` on a tracked source file truncates it. Append instead, or
   `git checkout --` to recover.
5. Chunk deletes must be scoped by **exact `resource_id` equality** — never by
   course, prefix, or time range.
6. Gemini free tier: 100 embeddings/min, 15 req/min. `429 RESOURCE_EXHAUSTED`
   is expected under load. Wait ~90 s and retry once; do not retry-loop.
7. Multiple agents killing uvicorn on 8123 mid-ingest will corrupt a job.
   Sequence server restarts.
8. `POST /lms/session` requires `learner_id`, `course_id`, `resource_id`,
   `resource_scope`, `current_time_ms`, `issued_at` (epoch **seconds**).
   `POST /chat` requires `session_token` in the body **and** the
   `X-Session-Token` header. Getting this wrong produces 401/422 that look like
   app bugs.
9. `ChatPanel` must not prefix every refusal with "I can't answer from watched
   content yet" — it contradicted the honest scope message.

## 8. Servers

```powershell
# API — always start from apps/api (env file resolves relative to the package)
cd apps/api
& "C:\Users\Nimish-2\AppData\Local\Programs\Python\Python314\python.exe" `
  -m uvicorn app.main:app --port 8123

# Web
cd apps/web; npm run dev -- --port 3000
```

`apps/web/.env.local` — `NEXT_PUBLIC_API_URL=http://localhost:8123`.
`apps/api/.env` — gitignored; never commit or echo its values.

## 9. Known gaps — honest, not papered over

- **No delete-resource endpoint or UI.** `purge_resource` exists but has no
  caller, and it does **not** detach from subjects. Exposing it needs a
  course-ownership check *and* subject detach first.
- **YouTube ingest cannot be verified in this environment** (captions/download
  unavailable).
- **Sessions are in-memory.** Postgres + pgvector is the planned migration —
  see `docs/adr/ADR-002-retrieval-stack-library-migration.md`.
- **Only one 11-minute video has been exercised end to end.** No long-form,
  multi-page-doc, or second-subject coverage.
- **Dev probe data is still present** in the store and on disk: `r1`,
  `res_probe`, `coverageprobe`, `titleprobe`, `imageprobe`.
- Optional next: ingest a shorter clip (4:30–6:00) so `dy` is reachable without
  five minutes of scrubbing; a second subject with a PDF and an image would
  exercise the untested non-video paths.

## 10. Reference files

- `docs/adr/ADR-002-retrieval-stack-library-migration.md` — library swap decision
- `docs/adr/ADR-001-lms-integration-scalability.md` — LMS boundary decision
- `docs/api.md`, `docs/architecture.md`, `docs/deployment-guide.md`
- `apps/api/app/retrieval/service.py` — retrieval + strict-T gate
- `apps/api/app/retrieval/recency.py` — recency preference
- `apps/api/app/chat/service.py` — prompt + citation validation
- `apps/api/app/ingestion/` — `visual.py`, `liveframe.py`, `images.py`,
  `documents.py`, `purge.py`, `backoff.py`, `embedder.py`
- `apps/web/components/ChatPanel.tsx`, `LearnSessionProvider.tsx`,
  `MarkdownBody.tsx`, `FileIngestForm.tsx`
- `apps/web/app/(learn)/ai-tutor/subjects/[subjectId]/content/[resourceId]/page.tsx`