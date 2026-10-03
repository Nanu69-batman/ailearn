# AI Learning Assistant

Timestamp-aware AI learning assistant that sits alongside an existing LMS.

## Structure

- `apps/api` — FastAPI backend (stateless; config via env, schema in `migrations/`)
- `apps/web` — Next.js 15 frontend
- `docs/` — problem statement, implementation plan, ADRs, research notes
- `docs/adr/ADR-001-lms-integration-scalability.md` — LMS boundary & scalability decision

## Run (prototype)

Local dev (see `docs/deployment-guide.md` §0 for prereqs):

```powershell
cd apps/api
pip install -r requirements.txt
uvicorn app.main:app --reload
# in another shell:
cd apps/web && npm install && npm run dev
```

Env templates: `apps/api/.env.example`, `apps/web/.env.example`
(`NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_VIDEO_URL`).
API reference: `docs/api.md`. Architecture: `docs/architecture.md`.

## Deploy (prototype: Vercel + Render + Neon + R2/S3)

Step-by-step with per-service env tables, scale-out triggers, and the
demo checklist (seeded DB, `/health` 200, CORS, keys, rollback):
**`docs/deployment-guide.md`**.

See `docs/IMPLEMENTATION_PLAN.md` for the full plan.
