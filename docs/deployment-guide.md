# Deployment Guide

This is how the live demo is deployed:

| Part | Where | URL |
|---|---|---|
| Web (Next.js 15) | Vercel, root dir `apps/web` | https://ailearn-theta.vercel.app |
| API (FastAPI) | Render web service, Docker, free tier | https://malai-pioneer-api.onrender.com |
| Vectors | Pinecone serverless index `ailearn` (3072-d, cosine) | — |
| Models | Gemini (answers, embeddings, vision), Groq (speech to text) | — |

The API can't run on Vercel. Its Python function bundle limit is 250 MB, which
faster-whisper, PyAV and chromadb exceed, and ingest runs as background jobs for
minutes. Render's free disk is wiped on every restart, which is why the vector
index lives in Pinecone. Files stay on the container disk: the demo video is
baked into the image, and files uploaded on the hosted site last until the next
restart.

Everything below has been done once for the live demo. Follow it in order to
reproduce it.

## 0. Prerequisites

- A GitHub repository with this code. Render and Vercel build from it.
- Accounts: Render, Vercel, Pinecone, Google AI Studio (Gemini key), Groq.
- A launch secret shared by the web app and the API:
  `python -c "import secrets; print(secrets.token_hex(32))"`.
- Local checks pass: `cd apps/api && python -m pytest -q && python -m ruff check .`

## 1. Pinecone: create the index and seed the demo

1. Create an API key in the Pinecone console.
2. Put it in `apps/api/.env` as `PINECONE_API_KEY=...`. Never commit that file.
3. With the demo video already ingested locally (see [DATASET.md](DATASET.md)), copy its chunks
   and their vectors into Pinecone:

   ```powershell
   cd apps/api
   python scripts/seed_pinecone.py res_2e629d682a3d
   ```

   The script creates the index `ailearn` (serverless, aws us-east-1, cosine,
   dimension taken from the local vectors: 3072 for `gemini-embedding-2`) if it
   doesn't exist. It copies vectors as they are, so it spends **no** embedding
   requests. Re-running it replaces that resource's chunks, deleting them by
   exact `resource_id` equality.

## 2. Render: the API

The repo root holds a Blueprint, `render.yaml`. It builds
`apps/api/deploy/render/Dockerfile` with the repo root as context. The image
bakes in `test-data/Partial-derivatives.mp4` as resource `res_2e629d682a3d` and
the seed subject `apps/api/deploy/seed/subjects.json`. `.dockerignore` limits the
build context to exactly those files plus `apps/api`.

1. Render dashboard -> **New -> Blueprint** -> pick the repository.
2. Render reads `render.yaml` and asks for the values marked `sync: false`:

   | Name | Value |
   |---|---|
   | `LLM_API_KEY` | Gemini key |
   | `EMBEDDING_API_KEY` | The same Gemini key |
   | `GROQ_API_KEY` | Groq key |
   | `PINECONE_API_KEY` | Pinecone key |
   | `LMS_LAUNCH_SECRET` | The secret from section 0. It must match Vercel's |
   | `CORS_ORIGIN` | The exact Vercel origin, e.g. `https://ailearn-theta.vercel.app` (no trailing slash) |

   `render.yaml` already sets the rest:

   | Name | Value | Why |
   |---|---|---|
   | `VECTOR_STORE` | `pinecone` | The index lives outside the container |
   | `PINECONE_INDEX` | `ailearn` | |
   | `ALLOW_UNSIGNED_LAUNCH` | `false` | Only launches signed by the web app's server route are accepted |
   | `LOCAL_WHISPER_FALLBACK` | `false` | The local Whisper model does not fit in 512 MB. If Groq fails, the window fails visibly instead of skipping speech |
   | `MAX_UPLOAD_MB` / `MAX_VIDEO_MINUTES` / `MAX_UPLOADS_PER_HOUR` | `100` / `15` / `10` | Protect the free model quota |

3. The first build takes several minutes. Watch **Logs** until you see
   `pinecone store ready (pinecone:ailearn, dim=3072)` and uvicorn listening.
4. Check it:

   ```bash
   curl https://malai-pioneer-api.onrender.com/health          # {"status":"ok"}
   curl https://malai-pioneer-api.onrender.com/courses/demo/resources
   ```

   The second command must list `res_2e629d682a3d`, `Partial-derivatives.mp4`.

The free plan sleeps after about 15 idle minutes. The first request after that
takes up to a minute while the service wakes, and the wake-up wipes the disk.

## 3. Vercel: the web app

1. **Add New -> Project** -> import the repository. Set **Root Directory** to
   `apps/web`. The framework preset is Next.js.
2. Environment variables (Production):

   | Name | Value |
   |---|---|
   | `NEXT_PUBLIC_API_URL` | `https://malai-pioneer-api.onrender.com` (no trailing slash) |
   | `LMS_LAUNCH_SECRET` | Same value as on Render. **Server-only**: never prefix it with `NEXT_PUBLIC_` |

3. Deploy. `app/api/launch/route.ts` signs each LMS launch with the secret on the
   server (HMAC-SHA256 over the same canonical payload as
   `apps/api/app/chat/launch.py`), so the secret never reaches the browser.
4. If the Vercel URL differs from the one in `CORS_ORIGIN`, update `CORS_ORIGIN` on Render.

## 4. Smoke test (all were run against the live deployment)

```bash
A=https://malai-pioneer-api.onrender.com; W=https://ailearn-theta.vercel.app

# CORS preflight from the web origin -> 200 + access-control-allow-origin: $W
curl -s -D - -o /dev/null -X OPTIONS $A/lms/session -H "Origin: $W" \
  -H "Access-Control-Request-Method: POST" -H "Access-Control-Request-Headers: content-type"

# Signed launch via the web app's server route -> 200 with session_id + session_token
S=$(curl -s -X POST $W/api/launch -H "content-type: application/json" \
  -d '{"learner_id":"judge","course_id":"demo","resource_id":"res_2e629d682a3d","resource_scope":["res_2e629d682a3d"],"current_time_ms":0,"issued_at":0}')
curl -s -X POST $A/lms/session -H "content-type: application/json" -d "$S"

# Unsigned launch -> 401 {"detail":"invalid launch"}; a tampered signature -> 401 too

# Media streams with Range -> 206
curl -s -o /dev/null -w "%{http_code}\n" -r 0-1023 $A/resources/res_2e629d682a3d/media
```

Then use the web app:
1. Open the **Engineering Mathematics** subject and play `Partial-derivatives.mp4`.
2. Pause at about 0:30 and ask about the end of the video. The tutor says the
   lesson hasn't got there yet, and every citation ends before 0:30.
3. Pause at about 5:00 and ask "What equation is on screen right now?" The answer
   cites moments just before the playhead.

## 5. Rollback

- **Web:** Vercel -> Deployments -> pick the previous good deployment -> **Promote
  to Production**.
- **API:** Render -> the service -> Events -> pick the previous deploy ->
  **Rollback**. Alternatively, revert the commit and push; `autoDeploy` rebuilds.
- **Vector index:** re-run `scripts/seed_pinecone.py` for the demo resource. It
  replaces that resource's chunks only.
- After any rollback, check that `/health` returns 200 and run the smoke test.

## 6. Known operational limits

- **Gemini free tier:** about 1,000 embedding requests per day per Google
  project. Every question costs one. When the quota is spent, `/chat` and ingest
  return `503` with the reset time. Switch to a key from another project, or
  enable billing.
- **Files on the container disk** disappear on restart or wake-up. Their chunks
  stay in Pinecone, but the media route then returns 404 for them. The demo video
  is unaffected.
- **YouTube ingest** is often blocked from datacenter IPs. Upload files instead.
- **One process:** ingest job status and the in-flight window guard live in
  memory. Moving that state out of the process is Phase A of
  [SCALING_PLAN.md](SCALING_PLAN.md).
