# Deployment Guide — AI Learning Assistant MVP (Prototype)

Target topology (§9 plan, `research-deployment.md`): **Vercel (web) + Render (API, Docker) + Neon (Postgres + pgvector) + R2/S3 (video)**. Code and config only — no live deploy is performed by this guide.

## 0. Prerequisites

- GitHub repo with `apps/api` (Dockerfile present) + `apps/web`.
- Accounts: Vercel, Render, Neon, Cloudflare R2 (or AWS S3), hosted LLM provider.
- Local checks green: `cd apps/api && ruff check . && pytest -q`.

## 1. Neon (Postgres + pgvector)

1. Create project → copy pooled connection string (`postgres://user:pass@host/db?sslmode=require`).
2. In Neon SQL editor run `CREATE EXTENSION IF NOT EXISTS vector;`.
3. Apply schema: `psql "$DATABASE_URL" -f apps/api/migrations/*.sql` (whichever migration files exist), then seed the demo course (ingestion CLI / seed script) and verify chunk + embedding row counts.
4. Create a second `*_test` database or branch for CI; rotate credentials per environment.

| Var (Neon dashboard → connection string) | Used as | Example |
|---|---|---|
| Connection string | `DATABASE_URL` on Render (API) | `postgresql+asyncpg://user:pass@ep-xxx.neon.tech:5432/ailearn?sslmode=require` |

## 2. Render (FastAPI via Docker)

1. New → Web Service → point at repo, root dir `apps/api`, runtime **Docker**, Dockerfile path `apps/api/Dockerfile`.
2. Plan: Starter for demo (free sleeps — expect cold starts); health check path `/health`.
3. Set env vars (table below), then deploy; note the public URL `https://<svc>.onrender.com`.
4. Verify: `curl https://<svc>.onrender.com/health` → `{"status":"ok"}`; check logs carry `x-request-id` JSON lines.

| Var (Render dashboard → Environment) | Required | Value / notes |
|---|---|---|
| `DATABASE_URL` | yes | Neon pooled string with `+asyncpg` driver prefix; must include `sslmode=require` |
| `LLM_API_KEY` | yes | Hosted LLM key; must be funded |
| `EMBEDDING_API_KEY` | if split | Same as LLM key if single provider; else embedding provider key |
| `CORS_ORIGIN` | yes | Exact Vercel URL, e.g. `https://ailearn-web.vercel.app` (no trailing slash) |
| `LMS_LAUNCH_SECRET` | yes | Random 32+ bytes; **never** `dev-secret` outside local |
| `PORT` | no | Set by Render automatically; Dockerfile honors `$PORT` |

## 3. Vercel (Next.js frontend)

1. Import repo → root dir `apps/web`, framework Next.js, build `next build`.
2. Set env vars (table below) for Production (+ Preview if demoing PRs), deploy, note `https://<proj>.vercel.app`.
3. Verify: page loads, video streams, pause → ask returns citations, citation click seeks.

| Var (Vercel dashboard → Settings → Env Vars) | Required | Value / notes |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | yes | Render URL, e.g. `https://ailearn-api.onrender.com` (no trailing slash) |
| `NEXT_PUBLIC_VIDEO_URL` | yes | R2/S3 public or signed CDN URL for the demo lecture |

## 4. Video (R2 / S3 + CDN)

1. Upload lecture MP4 (+ `.vtt` transcript if available) to R2 bucket (public) or S3 + CloudFront.
2. Confirm byte-range streaming: `curl -r 0-1023 -o /dev/null -w '%{http_code}\n' <VIDEO_URL>` → `206`.
3. Paste URL as `NEXT_PUBLIC_VIDEO_URL`; ensure the transcript used for ingestion matches this asset (sha256 skip-guard in ingestion).

## 5. Wire-up & CORS check

1. `CORS_ORIGIN` on Render must exactly equal the Vercel origin. After both deploys, open browser devtools → ask a question → no CORS preflight errors; response carries `x-request-id`.
2. If embedding the widget in a real LMS page, add that LMS origin too (comma handling: current code takes a single origin — extend to list before multi-origin demos).

## 6. Scale-out triggers (when to leave prototype)

Stay on single-instance + single Neon DB until **measured** load says otherwise (`IMPLEMENTATION_PLAN.md` §14). Promote when any trigger fires, in this order: API replicas (stateless — safe) → PgBouncer/pool tuning → read replicas → dedicated ingestion worker → retrieval cache `(resource_id, T bucket, query-hash)` short TTL → `VectorStore` swap to Qdrant/OpenSearch. Record each move in an ADR update.

| Signal | How measured | Threshold to act |
|---|---|---|
| Chat p95 latency | k6/Locust 25/50/100 concurrent learners, per-stage logs | Sustained p95 over demo budget |
| DB pool exhaustion | SQLAlchemy pool logs / errors under load | Any pool-timeout errors |
| Vector CPU saturation | HNSW query latency vs chunk count 1k→50k | Degradation curve knee reached |
| Ingestion interference | Full-course ingest while learners query | API p95 degrades beyond budget or job stalls |

## 7. Demo checklist (run in order, all must pass)

- [ ] Neon DB migrated + seeded (courses/resources/chunks + embeddings present; counts match ingest report).
- [ ] Video URL returns `206` and plays in the Vercel page.
- [ ] `GET /health` → `200 {"status":"ok"}` on Render (and locally).
- [ ] CORS: Vercel origin asks successfully; no preflight failures; `x-request-id` present.
- [ ] LLM + embedding keys set and funded (one real grounded answer observed, citations clickable).
- [ ] LMS launch: signed launch → `200` + `session_id`; tampered signature → `401`; stale `issued_at` → `401`.
- [ ] Timestamp gate spot-check: pause mid-video, ask about later content → `refused: true`.
- [ ] Fallback: recorded end-to-end video ready in case of live-demo gremlins.
- [ ] Rollback: previous good Vercel deployment + previous good Render deploy both identified; rollback = **Promote previous deployment** on each dashboard (DB migrations are additive-only for the demo — never destructive mid-demo). Verify `/health` 200 after any rollback.
