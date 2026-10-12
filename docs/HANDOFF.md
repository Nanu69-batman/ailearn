# Handoff — AI Learning Assistant (Malai Pioneer)

Last updated: 2026-10-12. Read this before touching the repo.
Binding operating rules live in `AGENTS.md` at the repo root.

---

## 1. Verified current state

| Check | Result |
|---|---|
| Backend tests | **177 pytest pass** |
| ruff | clean |
| Web `tsc --noEmit` | clean |
| Web `npm run build` | pass |
| Web `npm run contract-check` | **PASS** |
| Stack | FastAPI + Chroma, Next.js 15 + Tailwind v3 |

Ports: API `8123`, web `3000`.

### Live system state (as ingested)

| Item | Value |
|---|---|
| Subject | `sub_06dea46b30bf4ee7` — "Engineering Mathematics" |
| course_id | `demo` |
| Resources | exactly one — `res_95f6dc70e36a`, `Partial-derivatives.mp4` |
| Chunks for that resource | 129 (89 transcript + 40 visual) |
| Duration | 655.5 s |
| Chroma inventory | 339 chunks total: `r1` (89), `res_95f6dc70e36a` (129), `res_probe` (121) |

`r1` and `res_probe` are known gitignored dev probe data. **Leave them alone.**

---

## 2. Routing

| Route | Page |
|---|---|
| `/` | landing, one button to `/dashboard` |
| `/dashboard` | subjects overview |
| `/ai-tutor/subjects` | AI Tutor nav target |
| `/ai-tutor/subjects/[subjectId]` | subject page |
| `/ai-tutor/subjects/[subjectId]/content/[resourceId]` | player + chat |

Legacy `/subject/[courseId]...` and `/demo` redirect — see
`apps/web/next.config.mjs`. There is no `/demo` page.

---

## 3. Stack

**Backend** — FastAPI on `:8123`. Chroma vector store in `apps/api/app/retrieval/`.
Gemini `gemini-3.5-flash-lite` for answers, `gemini-embedding-2` for embeddings.

**Ingestion** (`apps/api/app/ingestion/`):

- faster-whisper for audio — timestamps come from the audio signal.
- Gemini vision on sampled frames — timestamps come from frame-index
  arithmetic, never model output. A model timeline overshooting the real
  duration is rejected.
- LangChain text splitters; pypdf / python-pptx for documents.
- YouTube via youtube-transcript-api. Download/captions are unavailable in this
  environment, so that path cannot be verified live.

**Frontend** — Next.js 15 + Tailwind v3 on `:3000`.

---

## 4. Verified behaviour (live probes)

| question | T (ms) | result | cited end_ms | modality |
|---|---|---|---|---|
| What equation is on screen? | 30000 | answered `$f(x,y) = x^2 y + \sin(y)$` | 28000 | visual |
| What is written on the board? | 2000 | REFUSED — strict-T correct | - | - |
| What colour is the curve in the diagram? | 600000 | answered "blue" | 76000 | visual |
| what is a partial derivative? | 300000 | answered | 23000, 284010 | transcript, transcript |

Every citation satisfies `end_ms <= T`. No gate breaches.

---

## 5. Safety invariants — must not be weakened

1. **Strict timestamp gate.** A passage is kept only when `end_ms <= T`. Never
   relax to `start_ms`. NULL `end_ms` excludes. An earlier eval measured a 48%
   production-gap rate with a loose gate.
2. **Citations are validated** against the retrieved set. Uncited or
   unknown-ID answers are refused, not passed through.
3. **Plausible-looking ids are not real ids.** A scope of `["res_video", ...]`
   matches no indexed chunk and silently refuses every query. Verify scope ids
   correspond to actually-indexed chunks.
4. **Scope is intersected, not replaced.** `focus_resource_id` must be
   intersected with the granted `resource_scope`. Replacing it was a real
   past bug.
5. **Secrets.** `apps/api/.env` holds Gemini keys and the launch secret. Never
   commit, echo, or read it into a brief.
6. `ALLOW_UNSIGNED_LAUNCH=true` and `LMS_LAUNCH_SECRET=dev-secret` are
   localhost-demo only. Production requires real HMAC-signed launches.

---

## 6. Architectural decisions worth recording

- The strict-T gate and citation validation were deliberately **not** outsourced
  to a library — they encode product policy that no library provides.
  Everything else (transcription, vision, chunking, vector store, splitters)
  uses proven libraries. See `docs/adr/ADR-002-retrieval-stack-library-migration.md`.
- `scope_empty` on `/chat` distinguishes "session grant doesn't cover this
  resource" from ordinary low-evidence refusal, so the UI stops blaming the
  learner for a stale grant.
- `LearnSessionProvider` persists `ailearn.session_resource_ids` and re-mints the
  session when that set no longer matches. Before this, the grant was written
  once and `sessionStorage` outlived every reload, so a re-ingest locked the
  learner out of chat permanently.
- `ChatPanel` must not prefix every refusal with "I can't answer from watched
  content yet" — it contradicted the honest scope message.
- Citations carry `modality` (`"transcript"` | `"visual"`) from the chunk's
  stored `kind`. An unknown kind maps to None and is never defaulted to
  "transcript".
- Recency: present-tense questions ("what's on screen right now") prefer chunks
  near T, applied strictly downstream of the gate. Attribute questions
  (colour/shape/label) are deliberately **not** recency-seeking.

---

## 7. Known issues and gotchas

1. **Do not run `npm run build` while `npm run dev` is running.** They share
   `.next` and corrupt it (`Cannot find module './571.js'`,
   `__webpack_modules__[moduleId] is not a function`, 500s). Recovery: kill
   node, delete `apps/web/.next`, restart.
2. **Green tests have repeatedly hidden broken behaviour** here — a missing
   React import passed `tsc`; a ChatPanel prefix bug shipped past a green suite;
   a cleanup agent reported "exactly one resource" when there were two.
   **Live probes are mandatory before claiming success.**
3. **Gemini free-tier rate limit**: `429 RESOURCE_EXHAUSTED` on
   `embed_content_free_tier_requests` (100/min). Wait ~90 s and retry once; do
   not retry-loop.
4. Multiple agents killing uvicorn on 8123 mid-ingest will corrupt a job.
   Sequence server restarts.
5. `Set-Content` on a tracked source file truncates it. Use append, or
   `git checkout --` to recover.
6. Deleting chunks must always be scoped by an exact `resource_id` equality
   filter — never by course, prefix, or time range.

---

## 8. Servers

```powershell
# API — always start from apps/api (env file is resolved relative to the package)
cd apps/api; python -m uvicorn app.main:app --port 8123

# Web
cd apps/web; npm run dev -- --port 3000
```

`apps/web/.env.local` — `NEXT_PUBLIC_API_URL=http://localhost:8123`.
`apps/api/.env` — gitignored; never commit or echo its values.

---

## 9. Not done / next

- Optional: ingest a shorter clip (4:30–6:00) so `dy` is reachable without
  five minutes of scrubbing.
- Untested in this environment: YouTube ingest end-to-end (no captions or
  download available).

---

## 10. Reference files

- `docs/adr/ADR-002-retrieval-stack-library-migration.md` — library swap decision
- `docs/adr/ADR-001-lms-integration-scalability.md` — LMS boundary decision
- `docs/api.md`, `docs/architecture.md`, `docs/deployment-guide.md`
- `apps/api/app/retrieval/service.py` — retrieval + strict-T gate
- `apps/api/app/retrieval/recency.py` — recency preference
- `apps/api/app/chat/service.py` — prompt + citation validation
- `apps/api/app/ingestion/` — audio, visual, chunking, embedder
- `apps/web/components/ChatPanel.tsx`, `LearnSessionProvider.tsx`
- `apps/web/app/(learn)/ai-tutor/subjects/[subjectId]/content/[resourceId]/page.tsx`