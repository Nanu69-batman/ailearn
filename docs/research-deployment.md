# Deployment Plan — AI Learning Assistant (Hackathon Demo)

Stack: Next.js 15 frontend, Python FastAPI backend, Postgres + pgvector, Whisper/ingestion, hosted LLM API.
Goal: publicly accessible demo, low cost, fast to ship.

## 1. Hosting Topology (recommended)

**Primary: Vercel (frontend) + Render (backend) + Neon (Postgres).**

| Component | Platform | Why |
|---|---|---|
| Next.js frontend | **Vercel** hobby | Zero-config, preview URLs, edge CDN, free SSL |
| FastAPI backend | **Render** free/Starter web service | Docker/native Python, public HTTPS URL, easy env vars |
| Postgres + pgvector | **Neon** free tier | Serverless Postgres, `pgvector` extension available, no ops |
| Whisper ingestion | Run locally/one-off job, or Render background one-off | Avoid long-running cost; pre-compute transcripts before demo |

**Alternative:** Railway (single project for FE+BE+PG, simplest dashboard) or Fly.io (cheaper always-on, more config). **Avoid AWS ECS/EC2 for demo** — higher setup cost of time; acceptable follow-up: AWS App Runner (BE) + RDS Serverless v2 (PG) + S3/CloudFront.

Full AWS demo path (documented, not primary): Amplify Hosting → App Runner → RDS Postgres → S3 → Secrets Manager → CloudWatch. Feasible but adds ~1 day of setup.

## 2. Managed Postgres / Vector

- **Neon** (default): `CREATE EXTENSION vector;`, store transcript chunks with `(video_id, start_ts, end_ts, embedding, text)`, HNSW or IVF-Flat index on `embedding`.
- Supabase as equivalent drop-in (also gives pgvector + auth if needed later).
- AWS-native: RDS Postgres with `pgvector`, or **pgvector on Aurora Serverless v2** for the AWS story.

## 3. Video Asset Hosting

- Upload the sample lecture to **Cloudflare R2** (no egress fees) or **AWS S3 + CloudFront**.
- Frontend uses native `<video>` / `react-player` / `vidstack` with the public CDN URL (or signed URL if keeping originality claims).
- Frontend reports `currentTime` on pause → backend uses timestamp for retrieval filter (`end_ts <= currentTime`).

## 4. Env / Secrets

- Never commit: `.env` in `.gitignore`; provide `.env.example` per service.
- Vars: `DATABASE_URL`, `LLM_API_KEY`, `EMBEDDING_API_KEY`, `VIDEO_CDN_BASE`, `CORS_ORIGIN`, `WHISPER_API_KEY` (if hosted).
- Store in Vercel/Render dashboards; AWS path uses Secrets Manager + IAM task roles.
- Validate all env vars at FastAPI startup (pydantic-settings); fail fast.

## 5. CI / Logging / Demo Checklist

- **GitHub Actions** (`.github/workflows/ci.yml`): FE job (`npm ci`, `lint`, `build`), BE job (`pip install`, `ruff`, `pytest`, Docker build smoke). Deploy via Vercel/Render Git integration on push to `main`.
- **Logging**: FastAPI structured JSON logs to stdout (Render captures); request-id middleware; simple `/health` endpoint. Frontend: Vercel analytics + console error logging.
- **Demo checklist**: Neon DB migrated + seeded; transcript embedded; video URL reachable via CDN; `/health` returns 200; Vercel env vars set; CORS allowlist correct; LLM key funded; fallback recorded-video of flow ready; rollback = redeploy previous Vercel/Render deployment.

Expected public URLs: `https://ailearn-web.vercel.app`, `https://ailearn-api.onrender.com`.
