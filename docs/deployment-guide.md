# Deployment guide

How to deploy Malai Pioneer on free tiers: the web app on **Vercel**, the API on a
**Hugging Face Space** (Docker), vectors in **Pinecone**, and files in
**Cloudflare R2**. Diagram and rationale: [ARCHITECTURE.md](ARCHITECTURE.md#a-hosting-topology).

Placeholders used below: `<WEB_URL>` is the Vercel URL and `<API_URL>` is the
Space URL (`https://<user>-<space>.hf.space`).

```
Browser -> Vercel (Next.js, signs LMS launch) -> HF Space (FastAPI :7860)
                                                   |-> Pinecone (vectors)
                                                   |-> Cloudflare R2 (files)
                                                   |-> Gemini (LLM, embeddings, vision)
                                                   '-> Groq (speech to text)
```

Why the API is not on Vercel: Vercel's function bundle limit is 250 MB, and
faster-whisper, PyAV and chromadb exceed it. Ingest jobs also run in the
background for minutes, and serverless functions have no persistent disk. A
Docker Space gives one long-lived container.

## 0. Prerequisites

| Account | Used for | Free tier is enough? |
|---|---|---|
| Google AI Studio | Gemini API key (LLM, embeddings, vision) | Yes, but quota-bound (see section 7) |
| Groq | `whisper-large-v3-turbo` transcription | Yes. Optional: without it the API falls back to local faster-whisper, which is much slower on a CPU Space |
| Pinecone | Serverless vector index | Yes |
| Cloudflare | R2 bucket | Yes |
| Hugging Face | Docker Space for the API | Yes (CPU basic) |
| Vercel | Next.js web app | Yes (Hobby) |

Generate one strong launch secret and use it in **both** Vercel and the Space:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

## 1. Pinecone index

1. Pinecone console -> **Create index**.
2. Settings:

   | Field | Value |
   |---|---|
   | Name | e.g. `malai-pioneer` (this becomes `PINECONE_INDEX`) |
   | Type | Serverless (any cloud/region on the free plan) |
   | Metric | **cosine** |
   | Dimensions | **3072** |

   The dimension must equal `settings.expected_embedding_dim()` in
   `apps/api/app/config.py`. With `EMBEDDING_API_KEY` set, that is
   `GEMINI_EMBED_DIM = 3072` (`gemini-embedding-2`). With no key it is the offline
   hashing embedder's 1536, which is not usable for a hosted index. A mismatched
   dimension makes every upsert fail.
3. **API keys** -> create a key. This is `PINECONE_API_KEY`.

## 2. Cloudflare R2 bucket

1. Cloudflare dashboard -> **R2** -> **Create bucket**, e.g. `malai-pioneer-media`
   (`R2_BUCKET`). Keep it **private**. Media is served through presigned URLs.
2. Note your **Account ID** from the R2 overview page (`R2_ACCOUNT_ID`).
3. **R2 -> Manage R2 API Tokens -> Create API token**: permission
   *Object Read & Write*, scoped to this bucket. Copy the **Access Key ID** and
   **Secret Access Key** (`R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`). The secret is
   shown once.
4. Bucket -> **Settings -> CORS policy** -> paste:

   ```json
   [
     {
       "AllowedOrigins": ["<WEB_URL>"],
       "AllowedMethods": ["GET", "HEAD"],
       "AllowedHeaders": ["Range"],
       "ExposeHeaders": ["Content-Length", "Content-Range", "Accept-Ranges", "ETag"],
       "MaxAgeSeconds": 3600
     }
   ]
   ```

   This is not optional. The player seeks with `Range` requests. "Ask about this
   frame" draws the `<video>` into a canvas, which needs
   `crossOrigin="anonymous"` and a CORS-approved media origin. Without this rule
   the capture fails **silently**: the canvas becomes tainted and no frame is
   sent. Add `http://localhost:3000` too if you test the local web app against R2.

## 3. Hugging Face Space (API)

1. huggingface.co -> **New Space**. SDK: **Docker** (blank template). Hardware:
   **CPU basic** (free). Visibility: public, so judges can open `<API_URL>/docs`.
2. The Space uses its own image, `apps/api/deploy/space/Dockerfile` (uid 1000,
   listens on **7860**), and `apps/api/deploy/space/README.md`, whose front matter
   declares `sdk: docker` and `app_port: 7860`. The deploy script in step 4 puts
   both at the Space root. You don't need to set `PORT`.
3. **Settings -> Variables and secrets.** Add each item below. Use **Secret** for
   anything sensitive.

   | Name | Kind | Value |
   |---|---|---|
   | `LLM_API_KEY` | secret | Gemini key |
   | `EMBEDDING_API_KEY` | secret | Same Gemini key |
   | `GROQ_API_KEY` | secret | Groq key |
   | `LMS_LAUNCH_SECRET` | secret | The secret generated in section 0 (same value as on Vercel) |
   | `PINECONE_API_KEY` | secret | From section 1 *(hosted only)* |
   | `R2_ACCESS_KEY_ID` | secret | From section 2 *(hosted only)* |
   | `R2_SECRET_ACCESS_KEY` | secret | From section 2 *(hosted only)* |
   | `ALLOW_UNSIGNED_LAUNCH` | variable | `false` |
   | `CORS_ORIGIN` | variable | `<WEB_URL>` (exact origin, no trailing slash) |
   | `VECTOR_STORE` | variable | `pinecone` *(hosted only)* |
   | `PINECONE_INDEX` | variable | Index name from section 1 *(hosted only)* |
   | `STORAGE` | variable | `r2` *(hosted only)* |
   | `R2_ACCOUNT_ID` | variable | From section 2 *(hosted only)* |
   | `R2_BUCKET` | variable | From section 2 *(hosted only)* |
   | `MAX_UPLOAD_MB` | variable | Upload size cap, e.g. `100` *(hosted only)* |
   | `MAX_VIDEO_MINUTES` | variable | Video length cap, e.g. `15` *(hosted only)* |
   | `MAX_UPLOADS_PER_HOUR` | variable | Global upload rate cap, e.g. `10` |
   | `LLM_MODEL` / `EMBEDDING_MODEL` / `GROQ_TRANSCRIBE_MODEL` | variable | Optional; the defaults are `gemini-3.5-flash-lite`, `gemini-embedding-2`, `whisper-large-v3-turbo` |

   The cap values above are suggestions, not tested limits. Choose them from your
   quota. The 11-minute demo video was indexed as 89 transcript chunks and 174
   visual chunks (from 164 sampled frames), each of which is embedded, per
   [HANDOFF.md](HANDOFF.md) and [EVALUATION.md](EVALUATION.md#c-performance).
   Never put `ALLOW_UNSIGNED_LAUNCH=true` or `LMS_LAUNCH_SECRET=dev-secret` on a
   public Space.
4. **Push the API code to the Space.**

   ```powershell
   cd apps/api
   $env:HF_TOKEN = "<a Hugging Face write token>"
   python scripts/deploy_space.py --space <hf-user>/<space-name> --with-data
   ```

   The script stages `app/`, `migrations/`, `requirements.txt` and the Space's
   `Dockerfile`/`README.md` in a temporary folder, then uploads it with
   `huggingface_hub`, which handles large files without git-lfs. It also creates
   the Space if it doesn't exist. `--with-data` bakes the local demo index
   (`data/chroma`), `data/subjects.json` and the media of every resource a
   subject references into the image, so the demo works from first boot. Stop
   the local API first so the Chroma files are copied in a consistent state.
   Leave out `--with-data` once the Space uses Pinecone and R2. `.env` and chat
   sessions are never staged; all configuration comes from the Space's
   variables and secrets.
5. Watch **Logs** until you see `providers active: llm=gemini ...` and uvicorn
   listening on `0.0.0.0:7860`. The first build installs faster-whisper, PyAV and
   chromadb, so expect several minutes.
6. Check: `curl <API_URL>/health` returns `{"status":"ok"}`. Then open
   `<API_URL>/docs`.

## 4. Vercel project (web)

1. Vercel -> **Add New -> Project** -> import the GitHub repo.
2. **Root Directory:** `apps/web`. Framework preset: Next.js (detected). Leave the
   build and install commands at their defaults.
3. **Environment Variables** (Production and Preview):

   | Name | Value | Exposure |
   |---|---|---|
   | `NEXT_PUBLIC_API_URL` | `<API_URL>` (no trailing slash) | Public, inlined at build time |
   | `LMS_LAUNCH_SECRET` | Same secret as the Space | **Server-only.** Never prefix with `NEXT_PUBLIC_` |

   A Next.js server route uses `LMS_LAUNCH_SECRET` to HMAC-sign the
   `POST /lms/session` launch payload (format: [api.md, Auth and signing](api.md#auth-and-signing)),
   so the API can refuse unsigned launches.
4. Deploy. Copy the production URL. That URL is `<WEB_URL>`.
5. `NEXT_PUBLIC_*` values are baked in at build time. If you change one,
   **redeploy**.

## 5. Wire-up and CORS check

1. Set the Space variable `CORS_ORIGIN=<WEB_URL>`, then restart the Space. The API
   allows exactly one origin.
2. Make sure the R2 CORS rule lists the same `<WEB_URL>`.
3. Preflight check from a shell:

   ```bash
   curl -si -X OPTIONS "<API_URL>/chat" \
     -H "Origin: <WEB_URL>" -H "Access-Control-Request-Method: POST" \
     | grep -i access-control-allow-origin
   # expect: access-control-allow-origin: <WEB_URL>
   ```
4. Check that an unsigned launch is refused (this proves `ALLOW_UNSIGNED_LAUNCH=false`):

   ```bash
   curl -s -X POST "<API_URL>/lms/session" -H 'content-type: application/json' \
     -d '{"learner_id":"u1","course_id":"demo","resource_id":"r","resource_scope":["r"],"current_time_ms":0,"issued_at":'$(date +%s)'}'
   # expect: {"detail":"invalid launch"}
   ```

## 6. Smoke test checklist

| # | Check | Expected |
|---|---|---|
| 1 | `GET <API_URL>/health` | `200 {"status":"ok"}` |
| 2 | `<API_URL>/docs` | Swagger UI lists `/chat`, `/chat/live-frame`, `/courses/{course_id}/ingest`, `/subjects` and the other routes |
| 3 | Open `<WEB_URL>` and go to the dashboard | Subjects load. The browser console shows no CORS errors |
| 4 | Create a subject and upload `test-data/Partial-derivatives.mp4` in stream mode | Job moves `pending -> processing`. `processed_through_ms` grows |
| 5 | Pinecone console | The index's vector count increases |
| 6 | R2 bucket | An object `res_….mp4` exists |
| 7 | Play the video and seek | Video plays and seeks (Range works). The network tab shows the media coming from the R2 domain |
| 8 | Pause at about 0:30 and ask "What equation is on screen?" | Answer with citations at or before 0:30. Clicking one seeks the video |
| 9 | Pause at 0:02 and ask the same question | Refused: nothing has been shown yet |
| 10 | Press "Ask about this frame" | Answer with a `live` citation at the playhead |
| 11 | Upload `test-data/UNIT-1-001.pdf` to the same subject and ask about it | Answer cites `page_no` |
| 12 | Upload a file over `MAX_UPLOAD_MB` | `413` with a readable message |

## 7. Operating notes

- **Quotas.** The Gemini free tier allows about 100 embedding texts per minute and
  about 1,000 embedding requests per day. When a daily quota is spent, the API
  returns `503 {"detail": "... quota reached ... Stopped instead of waiting."}`
  and the ingest job fails with the same text. Wait for the reset, or use a
  paid key. Do not keep retrying.
- **Space sleep and restarts.** Free Spaces sleep when idle, and the container
  disk is ephemeral. Vectors (Pinecone) and files (R2) survive. In-memory state
  does not: ingest job status, the `/position` in-flight guard, and sessions and
  subjects (mirrored to local `data/` files, which are lost on rebuild). After a
  restart learners re-launch and get a fresh session. Videos that were mid-stream
  resume from the frontier stored in the vector metadata.
- **YouTube ingest** often fails from datacenter IPs. Use file upload on the
  hosted deployment.

## 8. Rollback

| Part | How to roll back |
|---|---|
| Web (Vercel) | **Deployments** -> pick the last good deployment -> **Promote to Production** (instant, no rebuild). |
| API code (Space) | Revert the bad commit in the Space repo (`git revert <sha>` and push, or use **Files -> History** in the Space UI). The Space rebuilds from the reverted commit. |
| API storage backends | If Pinecone or R2 misbehaves, **unset** `VECTOR_STORE` and `STORAGE` in the Space settings and restart. The API falls back to Chroma on the container disk (the demo index if one is baked into the image, otherwise empty) and to local file storage. Content uploaded only to Pinecone/R2 is not visible in this mode, and new uploads go to the ephemeral disk. Treat it as an emergency fallback for the demo. |
| Secrets | If a key leaks, rotate it at the provider (Google AI Studio, Groq, Pinecone, Cloudflare), update the Space secret or Vercel variable, and restart or redeploy. Rotating `LMS_LAUNCH_SECRET` invalidates all open sessions. Update it on **both** sides at the same time. |

## 9. Local development (for comparison)

No cloud services are needed apart from a Gemini key. Defaults are
`VECTOR_STORE=chroma` (on disk under `apps/api/data/`), files on disk, and
`ALLOW_UNSIGNED_LAUNCH=true` with `LMS_LAUNCH_SECRET=dev-secret` for the
localhost demo only. Run `run.bat` on Windows, or see the
[README quickstart](../README.md#quickstart-local).
