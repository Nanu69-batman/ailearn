# Deployment Guide

This is how the live demo is deployed:

| Part | Where | URL |
|---|---|---|
| Web (Next.js 15) | Vercel, root dir `apps/web` | https://ailearn-theta.vercel.app |
| API (FastAPI) | Render web service, Docker, free tier | https://malai-pioneer-api.onrender.com |
| Vectors | Pinecone serverless index `ailearn-llama` (1024-d, cosine), embedded by Pinecone Inference `llama-text-embed-v2` | — |
| Models | OpenRouter Nemotron (agentic tutor), Gemini (quiz writing, fallback answers, vision), Pinecone Inference (embeddings), Groq (speech to text) | — |

The API can't run on Vercel. Its Python function bundle limit is 250 MB, which
faster-whisper, PyAV and chromadb exceed, and ingest runs as background jobs for
minutes. Render's free disk is wiped on every restart, which is why the vector
index lives in Pinecone. Files stay on the container disk: the demo video and
the UNIT-1 PDF notes are baked into the image, and files uploaded on the hosted
site last until the next restart.

Everything below has been done once for the live demo. Follow it in order to
reproduce it.

## 0. Prerequisites

- A GitHub repository with this code. Render and Vercel build from it.
- Accounts: Render, Vercel, Pinecone, Google AI Studio (Gemini key), Groq,
  OpenRouter (optional; without it every question uses the Gemini pipeline).
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

   The demo PDF (`res_7c1f4a92d0e5`, 97 chunks) is in the same index, embedded
   with `llama-text-embed-v2` from its extracted page text.

   The script creates the index `ailearn` (serverless, aws us-east-1, cosine,
   dimension taken from the local vectors: 3072 for `gemini-embedding-2`) if it
   doesn't exist. It copies vectors as they are, so it spends **no** embedding
   requests. With `EMBEDDING_PROVIDER=pinecone` the index must be 1024-d
   instead: copy each chunk's text and metadata and re-embed the text with
   `llama-text-embed-v2` (passage mode); no Gemini call is needed. Re-running it replaces that resource's chunks, deleting them by
   exact `resource_id` equality.

## 2. Render: the API

The repo root holds a Blueprint, `render.yaml`. It builds
`apps/api/deploy/render/Dockerfile` with the repo root as context. The image
bakes in `test-data/Partial-derivatives.mp4` as resource `res_2e629d682a3d`,
`test-data/UNIT-1-001.pdf` as resource `res_7c1f4a92d0e5`, and the seed subject
`apps/api/deploy/seed/subjects.json`, which holds both. `.dockerignore` limits the
build context to exactly those files plus `apps/api`.

1. Render dashboard -> **New -> Blueprint** -> pick the repository.
2. Render reads `render.yaml` and asks for the values marked `sync: false`:

   | Name | Value |
   |---|---|
   | `LLM_API_KEY` | Gemini key |
   | `EMBEDDING_API_KEY` | The same Gemini key |
   | `GROQ_API_KEY` | Groq key |
   | `PINECONE_API_KEY` | Pinecone key |
   | `OPENROUTER_API_KEY` | OpenRouter key for the agentic tutor. Leave empty to use only the Gemini pipeline |
   | `OPENROUTER_MODEL` | `nvidia/nemotron-3.5-super-vl:free` (any model with tool calling) |
   | `AGENT_TIMEOUT_S` / `AGENT_TOTAL_TIMEOUT_S` | Optional. Unset (the default) means no time limit of our own on the agent: a slow free-tier reply is waited for instead of being cut off |
   | `LMS_LAUNCH_SECRET` | The secret from section 0. It must match Vercel's |
   | `CORS_ORIGIN` | The exact Vercel origin, e.g. `https://ailearn-theta.vercel.app` (no trailing slash) |

   `render.yaml` already sets the rest:

   | Name | Value | Why |
   |---|---|---|
   | `VECTOR_STORE` | `pinecone` | The index lives outside the container |
   | `PINECONE_INDEX` | `ailearn-llama` | 1024-d, must match the embedder's width (checked at start-up) |
   | `EMBEDDING_PROVIDER` | `pinecone` | Pinecone embeds chunks and queries; Gemini's ~1,000 texts/day embedding quota is not used |
   | `ALLOW_UNSIGNED_LAUNCH` | `false` | Only launches signed by the web app's server route are accepted |
   | `LOCAL_WHISPER_FALLBACK` | `false` | The local Whisper model does not fit in 512 MB. If Groq fails, the window fails visibly instead of skipping speech |
   | `MAX_UPLOAD_MB` / `MAX_VIDEO_MINUTES` / `MAX_UPLOADS_PER_HOUR` | `100` / `15` / `10` | Protect the free model quota |
   | `DEV_TRACE` | `true` (default) for the demo | Records pipeline steps for the Developer view (`GET /dev/trace`). The feed has no auth: set `false` when real learners use the site |

3. The first build takes several minutes. Watch **Logs** until you see
   `pinecone store ready (pinecone:ailearn-llama, dim=1024)` and uvicorn listening.
4. Check it:

   ```bash
   curl https://malai-pioneer-api.onrender.com/health          # {"status":"ok"}
   curl https://malai-pioneer-api.onrender.com/courses/demo/resources
   ```

   The second command must list `res_2e629d682a3d` (`Partial-derivatives.mp4`)
   and `res_7c1f4a92d0e5` (`UNIT-1-001.pdf`).

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

# The baked-in PDF -> 200 application/pdf (475959 bytes)
curl -s -o /dev/null -w "%{http_code} %{content_type}\n" $A/resources/res_7c1f4a92d0e5/media
```

Then use the web app:
1. Open the **Engineering Mathematics** subject and play `Partial-derivatives.mp4`.
2. Pause at about 0:30 and ask about the end of the video. The tutor says the
   lesson hasn't got there yet, and every citation ends before 0:30.
3. Pause at about 5:00 and ask "What equation is on screen right now?" The answer
   cites moments just before the playhead. Click a citation: the video seeks
   there and the cited span shows as a band on the scrub bar.
4. Open the **Quiz** tab and start a quiz. Multiple-choice and numerical answers
   are checked on Submit with no model call; a written answer gets feedback.
5. Open `UNIT-1-001.pdf`, ask about it, and click a page citation: the viewer
   opens the page with the cited text highlighted.
6. Tick the PDF as done in the course checklist, go back to the video, and ask
   something the notes cover: the answer can now cite PDF pages too.
7. Click **Developer view** in the header. In the new window, ask a question
   in the app: the AI Tutor diagram plays each step (scope check, search,
   timestamp gate, model, citation check), and clicking a step shows its data.
   Upload a short video and the Ingestion diagram shows transcription, frame
   description, chunking, embedding and storage for each 2-minute section.

## 5. Rollback

- **Web:** Vercel -> Deployments -> pick the previous good deployment -> **Promote
  to Production**.
- **API:** Render -> the service -> Events -> pick the previous deploy ->
  **Rollback**. Alternatively, revert the commit and push; `autoDeploy` rebuilds.
- **Vector index:** re-run `scripts/seed_pinecone.py` for the demo resource. It
  replaces that resource's chunks only.
- After any rollback, check that `/health` returns 200 and run the smoke test.

## 6. Known operational limits

- **Embeddings:** Pinecone Inference's free plan includes 5M
  `llama-text-embed-v2` tokens a month (250k/min for passages, 50k/min for
  queries). An 11-minute lecture is about 20k tokens. Repeated queries are
  cached in memory. Gemini's free tier (about 1,000 embedding texts a day) is
  only used when `EMBEDDING_PROVIDER` is not `pinecone`.
- **OpenRouter free tier:** 50 free-model requests a day per account (1,000
  after adding $10 of credit). When it is spent, OpenRouter answers `429` and
  every question goes to the Gemini pipeline until the reset at 00:00 UTC.
- **Gemini free tier:** frame and image descriptions, quizzes and fallback
  answers use Gemini. When that quota is spent, ingest and `/chat` return `503` with the
  reset time.
- **Files on the container disk** disappear on restart or wake-up. Their chunks
  stay in Pinecone, but the media route then returns 404 for them. The demo video
  and PDF are unaffected.
- **YouTube ingest** is often blocked from datacenter IPs. Upload files instead.
- **Subjects reset on restart.** The image seeds `data/subjects.json` from
  `apps/api/deploy/seed/subjects.json`, so a restart restores the
  **Engineering Mathematics** demo subject (`sub_06dea46b30bf4ee7`) even if it
  was deleted, and drops subjects created on the hosted site since. To get the
  demo subject back, use **Manual Deploy -> Restart service** on Render.
- **One process:** ingest job status and the in-flight window guard live in
  memory. Moving that state out of the process (Redis or Postgres) is the
  first step toward concurrent users.
