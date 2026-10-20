# Scaling plan — from one-process demo to an LMS module with concurrent users

Status: plan only, nothing here is implemented. Written 2026-10-20.
Related: `docs/adr/ADR-001-lms-integration-scalability.md`,
`docs/adr/ADR-002-retrieval-stack-library-migration.md`,
`docs/deployment-guide.md` (target topology).

## 0. Goal and what "ready" means

The tutor runs as a **separate service** that an LMS launches into (iframe or
deep link) with a signed launch. It must serve many learners at once, across
more than one server instance, without losing sessions or jobs when an
instance restarts.

**Ready means all of these are true:**

1. Two API instances behind a load balancer give the same answers, and killing
   one instance loses no session, no subject and no ingest job.
2. A load test at 25, 50 and 100 concurrent learners meets a stated p95 budget
   per stage, with **zero spoiler leakage and validated citations** under that
   load (the existing eval gates still pass).
3. An upload or a long ingest cannot slow down chat for other learners.
4. Every state-changing route checks who is calling and what role they have.
5. One tenant (school or LMS site) cannot read or change another tenant's data.

## 1. Where it stands today

Based on the code and `docs/HANDOFF.md`. Concurrent chat load has **not** been
measured, so the chat-path rows are expectations, not results.

| Area | Today | Why it blocks scale |
|---|---|---|
| Sessions | `InMemorySessionStore` guarded by a thread lock (`app/chat/sessions.py`) | A second instance or a restart loses sessions |
| Subjects | In-memory store persisted to `data/subjects.json` (`app/subjects/store.py`) | A single file written by one process |
| Ingest jobs | In-memory registry (`app/ingestion/jobs.py`) | Lost on restart; the "already running" check for the next window only sees its own process |
| Vector store | Chroma `PersistentClient` on local disk | Several workers contend for it; another machine cannot see it |
| Media files | Local `UPLOAD_DIR` | Only the instance that received a file can serve or process it |
| Ingest execution | Runs in the API process (thread), with CPU Whisper | Competes with live chat for CPU |
| Gemini quota | One token bucket per process (`_TextQuota`) plus a per-process vision limiter | N instances each assume the full allowance; free tier is about 100 texts/min and about 15 vision requests/min |
| Auth | Chat routes verify a session token; ingest, subjects, delete and position routes have no auth | Anyone who can reach the API can upload or delete |
| Tenancy | Scoped by `course_id` only | No tenant boundary |
| Launch | HMAC-signed launch exists; `ALLOW_UNSIGNED_LAUNCH` is a dev switch | Production must require signed launches |

## 2. Target architecture

```
LMS ──signed launch──▶ Web (static + SSR) ──▶ API (stateless, N instances)
                                              │        │
                                   Postgres + pgvector   Redis (rate limit, cache, queue)
                                   (sessions? no: tokens)         │
                                              │            Ingest workers (M instances)
                                       Object storage ◀──────────┘   (Whisper, vision, embed)
```

- **API**: stateless. Chat, retrieval, citation checks. Scales by adding instances.
- **Workers**: all heavy ingest (transcribe, frame description, embedding), pulled from a queue.
- **Postgres + pgvector**: chunks and vectors, subjects, resources, job records, tenants.
- **Redis**: shared rate limiter for Gemini, short-lived retrieval cache, queue backend.
- **Object storage** (S3 or R2): uploaded media and staged assets.

## 3. Work plan

Phases are ordered so each one is shippable and testable on its own. Sizes are
rough (S under 2 days, M 2-5 days, L over 5 days) for one engineer who knows the repo.

### Phase A — Stateless API (M)

- **Sessions as signed tokens.** The session token already carries and verifies
  the learner and resource scope. Make it self-contained (learner, course,
  resource scope, expiry) so no server-side lookup is needed; drop
  `InMemorySessionStore` or back it with Redis only for revocation.
- **Subjects to the database.** Replace `data/subjects.json` and
  `InMemorySubjectStore` with Postgres tables. Keep the current store interface
  so callers do not change.
- **Jobs to the database.** Persist job records (status, stage, windows done,
  processed-through) and look "is a window already running for this resource"
  up from the table, using a row lock or a unique constraint so two instances
  cannot queue the same window.
- **Acceptance:** run two API instances locally; restart one mid-session;
  the learner keeps chatting and a running job's status is still readable.
- **Risk:** the progress fields written after each window must be written by
  the worker, so Phase C depends on this.

### Phase B — Shared storage (L)

- **Postgres + pgvector** (ADR-002). Implement the existing `VectorStore`
  interface on pgvector. The strict gate (`end_ms <= T`, null end excluded)
  must move into the SQL `WHERE` clause **and** stay re-checked in Python
  afterwards, exactly as `ChromaVectorStore` does today.
- **Processed-through marker and duration** stay as chunk metadata, or become a
  `resources` table column. Pick one; the resource listing and the chat
  "not processed yet" branch read it.
- **Object storage for media.** `resolve_asset`, `stream_media`, delete and
  purge share one resolver today; keep that single seam and swap local disk
  for the bucket. Serve media through signed URLs or a streaming proxy.
- **Acceptance:** the eval harness (`python -m app.eval.harness`) gives the same
  leakage 0 and production-gap 0 on pgvector as on Chroma; deleting a resource
  removes chunks **and** the stored object, by exact id.
- **Risk:** this is the biggest change. Keep Chroma behind a config switch
  until the eval numbers match.

### Phase C — Separate ingest workers (L)

- Move `ingest_media_windows` and the document/image paths behind a queue
  (RQ, Celery or a cloud task queue). The API only validates the upload,
  stores the file, writes a job row and enqueues.
- Whisper, frame sampling, vision calls and embedding run in workers. Workers
  scale independently and can use bigger machines or a GPU.
- The position endpoint enqueues the next window instead of running it in the
  API process.
- Replace the per-process "active job" attribution with job-scoped context in
  the worker (already a context variable in the current code).
- **Acceptance:** while a worker ingests a long video, chat p95 on the API does
  not move; killing a worker mid-window leaves the window re-runnable (the
  range delete already makes re-runs safe).

### Phase D — Shared rate limiting and model quota (M)

- Move the embedding text quota and the vision request spacing/backoff gate
  from per-process objects to a Redis-backed limiter shared by every worker
  and API instance.
- Use a paid Gemini key. The free tier cannot serve many learners.
- Per-tenant budgets and a fair queue so one tenant's big upload cannot starve
  others. Surface a clear "queued, retrying" state instead of silent failure.
- Cache repeated retrievals by `(resource_id, T bucket, query hash)` with a
  short TTL (ADR-001), only for answers the gate already cleared.
- **Acceptance:** with 3 workers, total Gemini request rate stays under the
  configured limit in a 10-minute soak; a 429 pauses all workers.

### Phase E — Authorization and tenancy (M)

- Put the role (learner, teacher, admin) and a tenant id in the signed launch.
- Require a teacher/admin role for upload, delete and subject changes;
  require a valid session for position and chat; check the path `course_id`
  against the tenant. The delete route's "course must own the resource" check
  is the model for this.
- Add `tenant_id` to chunks, resources and subjects, and to every query's
  `WHERE` clause. Scope stays **intersected**, never replaced.
- Production refuses to start with `ALLOW_UNSIGNED_LAUNCH=true` or the
  `dev-secret` launch secret.
- Move to LTI 1.3 launches if the target LMS supports them.
- **Acceptance:** a test suite that tries cross-tenant reads, cross-tenant
  deletes and learner-calls-teacher-routes, and all fail closed.

### Phase F — Prove it (M)

- Load test (k6 or Locust): 25, 50, 100 concurrent learners asking questions,
  plus 2-3 concurrent ingests. Record p50/p95 per stage (retrieval, LLM call,
  citation check) as in ADR-001 and `docs/deployment-guide.md` section 6.
- Re-run the eval gates under multi-learner scopes: leakage 0, refusal >= 95%.
- Add structured logs with a request id, per-stage timings (the window timing
  already in job detail is a start), error-rate and queue-depth metrics, and
  alerts on queue depth and 429 rate.
- Record the numbers. **No latency or capacity claim goes in a slide until it
  is measured.**

## 4. Order and dependencies

```
A (stateless) ──▶ C (workers) ──▶ D (shared limits) ─┐
      │                                               ├─▶ F (prove it)
      └──▶ B (shared storage) ──────────▶ E (auth) ──┘
```

A is first because B, C and E all need jobs, sessions and subjects out of
process memory. B and C can then run in parallel on different people.

## 5. Things that must not regress

These are product properties, not preferences:

1. **Strict timestamp gate**: keep only `end_ms <= T`; null end excludes.
2. **Citations validated** against the retrieved set; uncited or unknown ids are refused.
3. **Scope intersected**, never replaced; ids verified against real chunks.
4. **Honest states**: partial, not-processed-yet, failed and skipped are stated, never implied.
5. **Exact-id deletes**: never by course, prefix or time range alone.
6. **Secrets** stay out of the repo and out of logs.

## 6. Open questions to decide early

- Which LMS first, and does it speak LTI 1.3 or only a custom signed launch?
- Hosted Postgres with pgvector (Neon, Supabase, RDS) or self-managed?
- Is a GPU worker for Whisper worth it, or use the Gemini transcription path
  per window? (The current Gemini fallback uploads the whole file, which is
  too costly per window.)
- Data retention: how long are uploads and chat logs kept, and who can delete them?
- Expected load: how many learners at peak, and how many hours of video are
  ingested per day? This sets worker count and the Gemini plan.

## 7. Cheapest first step if time is short

Do Phase A plus the Redis rate limiter from Phase D, run two instances, and
load-test chat only. That covers the biggest risks (lost state, quota blowups)
without the pgvector migration.
