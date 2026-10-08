# Handoff — AI Learning Assistant (Malai Pioneer)

Last updated: 2026-10-05. Read this before touching the repo.
Binding operating rules live in `AGENTS.md` at the repo root.

---

## 1. Verified current state

| Check | Result |
|---|---|
| Backend tests | **122 passed, 2 skipped** |
| ruff | clean |
| Web `tsc --noEmit` / build | clean |
| API `:8123` | 200 |
| Web `:3000/demo` | 200 |
| Unsigned dev launch | **200** (demo unblocked) |
| Backend tests | **136 passed**, 2 skipped |
| LLM provider | `llm=gemini` (`gemini-3.5-flash-lite`) |
| Citation markers | stripped from answers (incl. bunched form) |

### Verified behaviour (live probes, not just green tests)

| Scenario | Citations |
|---|---|
| Focus video, T=120s, `qa` | `video:30000-80000` |
| Focus video, T=120s, `summarize` | 3 video spans, all `end_ms <= T` |
| Focus PDF | `pdf` only — flips correctly |
| **Strict-T, T=0** | `pdf` only — no video leakage |
| **Strict-T, T=10s** | `pdf` only — gate holds |

Embedder is now `GeminiEmbedder` 3072-dim for **both** ingest and query. No
dimension-mismatch warning in server logs.

---

## 2. Root causes found this session — do not reintroduce

These each cost a full debug cycle. The notes exist so the failure modes stay
recognised. **In every case below, the test suite was fully green while the product
was broken.** Green tests are not evidence; live probes are.

### 2.1 Plausible-looking IDs that match nothing
Sessions were created with placeholder scope `["res_video","res_pdf","res_ppt"]`.
Those match no indexed chunk, so retrieval returned zero passages and **every**
answer was refused regardless of video position. Frontend now fetches real resource
IDs before session launch (`SessionBootstrap.tsx`).

**Symptom to recognise:** refusal with a valid timestamp and healthy-looking logs.

### 2.2 Ranking re-run after truncation
`InMemoryVectorStore.search` applies its own `fetch_k` before returning, so the
focus re-rank ran on an already-truncated pool — focused video chunks sat at ranks
26–40 of 44 and were already gone. Fixed by retrieving the focused resource as its
own scope-and-T-gated query, unioning it in, then truncating to `k`.

### 2.3 Silent dimension mismatch (the real culprit behind 2.2)
Snapshot held 3072-dim Gemini vectors; `RetrievalService.__init__` **hardcoded**
`HashingEmbedder()` (1536) even though a valid Gemini key was configured. `_cosine`
used `zip`, which truncates mismatched vectors into a plausible-looking meaningless
score. Fixed at source: `get_default_embedder()` everywhere, and `_cosine` now
raises `EmbeddingDimensionMismatch` instead of truncating.

**Symptom to recognise:** relevance scores that are plausible but garbage. Silent
numerical corruption is worse than an exception.

### 2.4 Config-only 401 on dev launches
`.env` had a 9-char non-dev `LMS_LAUNCH_SECRET`, so the `secret == DEV_SECRET`
guard failed and **all** unsigned launches 401'd — breaking the web UI, which sends
unsigned launches by design. The gating *code* was always correct; only the value
was wrong. Fixed in `.env` (gitignored). Production guard intact: a non-dev secret
still rejects unsigned launches.

**Lesson:** an agent worked around this for its own test instead of reporting it
broke the frontend. If a change requires signing to make your own probe pass,
check whether it broke the unsigned dev path.

### 2.5 `CITATION_RE` could not read a bunched marker
`CITATION_RE`'s class `[A-Za-z0-9_\-:, ]` omits `=`, so Gemini's real output
`[chunk_id=aaa, chunk_id=bbb]` never matched. Two consequences: markers were never
stripped from displayed text, **and** the video IDs inside them were never
extracted — so they were never validated or cited. Fixed with a dedicated
`MARKER_RE`; validation is now strictly stronger.

**Symptom to recognise:** a unit test with `[chunk_id=abc]` passes while production
output uses the bunched form. Test fixtures must use the shape the model emits.

### 2.6 `.env` was read from the wrong directory
`SettingsConfigDict(env_file=".env")` is **CWD-relative**. Running from the repo
root silently loaded a different `.env`, leaving `llm_api_key` empty and forcing
`DeterministicStubLLM`. Fixed by resolving the path absolutely from `__file__`.

**Symptom to recognise:** `providers active: llm=stub` while a valid key sits in
`apps/api/.env`. Always start the API from `apps/api`.

### 2.7 In-memory sessions + silent mock fallback (most deceptive)
Sessions lived only in memory, so any API restart made every open browser tab
return `404 session not found`. `ChatPanel` caught that and substituted a **mock
answer**, which is visually identical to a real one — the user saw canned
"refusals" with no indication anything was wrong.

**Symptom to recognise:** plausible-looking canned answers after a server restart.
Any error path that returns mock content must label it unambiguously, and a dead
session must surface as a dead session, never as content.

---

## 3. Next task (ready to delegate)

**Postgres + pgvector swap.** Current store is `InMemoryVectorStore` + snapshot
pickle. The focused-resource path now issues a second scoped query per request,
which is a second SQL round-trip once ported — needs an index on
`(course_id, resource_id)`. See `docs/adr/ADR-001-lms-integration-scalability.md`.

Smaller candidates:
- Dataset upload for judges
- Vercel deployment
- Mock interview extension (Phase 2)

---

## 4. Servers

```powershell
# API
cd apps/api; python -m uvicorn app.main:app --port 8123

# Web
cd apps/web; npm run dev -- --port 3000
```

**Gotcha:** `get_shared_store()` does **not** auto-load the snapshot outside FastAPI
startup. A bare `python -c` probe reports an empty store and will send you chasing
the wrong bug. Reproduce against the running server or the lifespan path.

**Next.js crash:** stale `.next` after heavy edits → kill node, `rm -rf .next`,
restart. Recurs often enough to expect it.

---

## 5. Config

- `apps/api/.env` — Gemini keys, `LMS_LAUNCH_SECRET=dev-secret`,
  `ALLOW_UNSIGNED_LAUNCH=true`. Gitignored; never commit or echo values.
- `apps/web/.env.local` — `NEXT_PUBLIC_API_URL=http://localhost:8123`
- Dev unsigned launch + dev secret is **localhost-demo only**. Production requires
  real HMAC-signed launches.

---

## 6. Safety invariants (see AGENTS.md Rule 6)

1. Strict-T: video passages kept only when `end_ms <= T`. Never relax to `start_ms`
   — a 48% spoiler rate was measured with a loose gate.
2. Citations validated against the retrieved set; uncited/unknown-ID answers are
   refused.
3. Scope enforced; a session can never retrieve outside its grant.
4. Secrets never in commits, logs, briefs, or reports.

---

## 7. Reference files

- `docs/IMPLEMENTATION_PLAN.md` — MVP architecture and phases
- `docs/adr/ADR-001-lms-integration-scalability.md` — LMS boundary decision
- `docs/api.md`, `docs/architecture.md`, `docs/deployment-guide.md`
- `apps/api/app/retrieval/service.py` — hybrid retrieval + focus path
- `apps/api/app/retrieval/store.py` — store, strict-T gate, `_cosine`
- `apps/api/app/chat/service.py` — retrieval → prompt → citation validation
- `apps/web/app/(learn)/[courseId]/page.tsx` — learn page
- `apps/web/lib/api.ts` — typed API client
- `apps/api/scripts/reindex_snapshot.py` — verified re-index path (not hand-built pickles)