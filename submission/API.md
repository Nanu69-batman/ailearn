# API Reference — AI Learning Assistant

Live contract for the FastAPI backend in `apps/api/`.

| | Hosted | Local |
|---|---|---|
| Base URL | `https://malai-pioneer-api.onrender.com` | `http://localhost:8123` |
| Swagger UI (try requests) | `https://malai-pioneer-api.onrender.com/docs` | `http://localhost:8123/docs` |
| ReDoc | `https://malai-pioneer-api.onrender.com/redoc` | `http://localhost:8123/redoc` |
| OpenAPI JSON | `https://malai-pioneer-api.onrender.com/openapi.json` | `http://localhost:8123/openapi.json` |

The web client reads the base URL from `NEXT_PUBLIC_API_URL`.
`apps/web/lib/api.ts` is the other half of the contract, so a change to one
needs a matching change to the other. Context: [ARCHITECTURE.md](ARCHITECTURE.md).

Conventions: JSON unless a route says `multipart/form-data`; errors use the
FastAPI shape `{"detail": "…"}`; `current_time_ms` is the learner clock in ms,
valid `0 .. 21600000` (6 h, clamped server-side); `issued_at` is **required**
(omitting it is a `422`; epoch ms canonical, epoch seconds auto-detected);
requests may carry `x-request-id` for log correlation. Rate limit: 60
req/min/IP across `/lms/session`, `/chat` and `/chat/live-frame` → `429`.
**Quota exhaustion:** if a Gemini daily quota is spent during any request (chat,
live frame or ingest), the API answers `503` with a readable reason and stops
instead of retrying:

```json
{"detail": "Gemini embedding quota reached (limit <N>, <quotaId>). It resets in about <H>h <M>m. Stopped instead of waiting."}
```

The message format comes from `quota_exhausted_message()` in
`app/ingestion/backoff.py`. `<quotaId>`, `<N>` and the reset time are copied from
the provider's reply. The prefix reads "Gemini request quota" for generation
calls and "API quota" when the provider is not identified. A background ingest job that hits the quota
reports the same text in its `error` field with `status: "failed"`, and
`/position` returns `status: "failed"` with that `error` until the reset time.

**Auth summary:** chat and history routes need a session token minted by a
signed `POST /lms/session`. Ingest, resource, subject and media routes are
currently unauthenticated (a planned follow-up). Hosted deployments
protect the model quota with `MAX_UPLOAD_MB`, `MAX_VIDEO_MINUTES` and
`MAX_UPLOADS_PER_HOUR` instead.

## Route table

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/health` | — | Liveness → `200 {"status": "ok"}`, no dependencies |
| GET | `/health/db` | — | Readiness → `200 {"status": "up"\|"down", "database_url_set": bool}` (real `SELECT 1`, 2 s timeout) |
| POST | `/lms/session` | HMAC launch | Open a session, mint a session token |
| POST | `/chat` | session token | Grounded Q&A over the granted course scope |
| POST | `/chat/live-frame` | session token | Answer about the frame currently on screen |
| GET | `/sessions/{session_id}/messages` | session token | Conversation history |
| POST | `/quiz` | session token | Practice quiz (MCQ, numerical, written) on what was covered |
| POST | `/quiz/grade` | session token | Grade one written quiz answer |
| POST | `/courses/{course_id}/ingest` | — | Ingest an uploaded file (multipart) |
| POST | `/courses/{course_id}/ingest-youtube` | — | Ingest a YouTube URL (JSON) |
| GET | `/ingestion/jobs/{job_id}` | — | Ingest job status |
| GET | `/courses/{course_id}/resources` | — | List a course's indexed resources |
| POST | `/courses/{course_id}/resources/{resource_id}/position` | — | Playhead heartbeat; queues the next stream-mode windows |
| DELETE | `/courses/{course_id}/resources/{resource_id}` | — | Delete a resource: detach from subjects, purge chunks and file |
| GET | `/resources/{resource_id}/media` | — | Stream a resource's asset (Range-capable) |
| GET | `/subjects` | — | List subjects |
| POST | `/subjects` | — | Create a subject (`201`) |
| GET | `/subjects/{subject_id}` | — | Read one subject |
| PATCH | `/subjects/{subject_id}` | — | Update a subject |
| DELETE | `/subjects/{subject_id}` | — | Delete a subject (grouping only) |
| GET | `/subjects/{subject_id}/resources` | — | List a subject's attached resources |
| POST | `/subjects/{subject_id}/resources` | — | Attach an existing resource to a subject |
| DELETE | `/subjects/{subject_id}/resources/{resource_id}` | — | Detach a resource from a subject |

## POST /lms/session

Opens a session from LMS launch context: verifies the HMAC signature, then
mints `session_id` + `session_token` (see **Auth and signing**).
Request (JSON, unknown fields rejected): `learner_id`, `course_id`,
`resource_id` — strings 1–256 chars, all required; `resource_scope` — string[]
of 1–256 **resource ids** (not types), required; `current_time_ms` — int
`0 .. 21600000`, required; `issued_at` — int, **required** (epoch ms; epoch
seconds auto-detected); `lesson_id` — string ≤256, optional; `signature` —
string ≤512, optional (required in production).
`200` → `{"session_id": "ses_…", "session_token": "…", "learner_id": "u1", "course_id": "c1"}`
Errors: `401 {"detail": "invalid launch"}` · `422` validation (empty scope,
negative `current_time_ms`, unknown field) · `429` rate limit.

## POST /chat

Grounded Q&A. The scope comes from the stored session, never from the client:
the session's `course_id` + `resource_scope`, filtered by the strict gate
`end_ms <= T`. Recent user turns only rewrite coreference; they never widen the
scope.
Request (JSON, unknown fields rejected): `session_id` — string, required,
unknown id → `404`; `session_token` — string, required, **on this route read
from the body field only** (see **Auth and signing**); `message` — string
1–4000 chars, required; `current_time_ms` — int `0 .. 21600000`, required;
`video_id` — string, optional, focus resource (a ranking preference that does
not widen the scope); `mode` — string, optional: `qa` (default) ·
`explain_simple` · `analogy` · `summarize` · `compare` · `quiz`.
`200` response: `answer`, `citations[]`, `refused`, `scope_empty`,
`live_frame` (`"live"` | `"document"` | null), `mode` (`"grounded"` |
`"supplemental"` | `"conversational"`), `grounded` (bool), `processing_pending` +
`processed_through_ms` (the playhead is past the processed part of a stream-mode
video, so the answer is an honest "not processed yet"), and `visual_pending` +
`visual_through_ms` (the transcript is indexed, but the on-screen descriptions
for this part are still being produced):

```json
{"answer": "The spike comes from the warmup step [chunk_id=ch_7f2a]. …",
 "citations": [{"chunk_id": "ch_7f2a", "source_type": "video", "modality": "transcript",
   "video_id": "vid_lecture_03", "start_ms": 150000, "end_ms": 180000,
   "page_no": null, "slide_no": null, "image_no": null, "live": false}],
 "refused": false, "scope_empty": false, "live_frame": null,
 "mode": "grounded", "grounded": true,
 "processing_pending": false, "processed_through_ms": null,
 "visual_pending": false, "visual_through_ms": null}
```

`mode: "supplemental"` appears only when the lesson has not covered the question
and the assistant teaches it from general knowledge. The answer text labels it
as not from the video, and it carries zero citations.

`mode: "conversational"` is a reply to small talk, such as "hi" or "who are
you". It has no retrieval, no citations and no lesson content.

Citation fields: `chunk_id` (always present); `source_type` (`video` · `pdf` ·
`pptx` · `image` · `note` · `code`); `modality` (`"transcript"` | `"visual"` |
`null` — how the evidence was produced; `null` means *not recorded*, not
"transcript"); `video_id`; `start_ms` / `end_ms` (`null` for images and
documents — they have no moment in time); `page_no` / `slide_no`; `image_no`
(1-based image position); `live` (`true` **only** for a live-frame capture,
where `start_ms == end_ms == T`).

### `scope_empty` vs. an ordinary refusal

Different failures; the UI relies on the difference. `refused: true` means
retrieval ran and found **not enough evidence**: no in-scope passage at or below
`T`, or the answer cited a chunk outside the retrieved set — the answer says so
and the citations are empty. `scope_empty: true` means the session grant **does
not cover the focused resource at all** (stale grant, deleted resource, or a
resource from another course): a grant problem, not a knowledge problem, where
the product refuses to answer from material the learner was never granted.
`scope_empty` is `true` only in that second case — it is not a synonym for
"refused", and a low-evidence refusal must never set it.

Errors: `404 {"detail": "session not found"}` · `401 {"detail": "invalid launch"}`
· `422` validation (empty message, message > 4000 chars, unknown mode, negative
`current_time_ms`, unknown field) · `429` rate limit · `502 {"detail": "upstream provider error"}`
· `503` provider quota spent (see Conventions).

### GET /sessions/{session_id}/messages

Conversation history in insertion order (user and assistant turns; assistant
turns carry validated citations). The token is read from the `session_token`
query parameter or the `X-Session-Token` header. `200` → `{"session_id": "ses_…",
"messages": [{"role": "user"|"assistant", "content": "…", "citations": [ … ],
"created_at": "2026-10-03T10:01:00.123456+00:00"}]}`. Errors: `404` · `401`.

## POST /chat/live-frame

Answers a question about the frame currently on the learner's screen: the
client captures the frame, the server describes it and injects it as
high-priority evidence alongside the retrieved passages.
Request — `multipart/form-data`: `session_id` (required), `current_time_ms`
(required int), `message` (required, 1–4000 chars), `frame` (required file: the
captured JPEG, ≤ 8 MB, `image/jpeg`); optional `session_token` (fallback for
the `X-Session-Token` header), `mode` (default `qa`, same values as `/chat`),
`video_id` (focus resource, defaults to the session's `resource_id`) and
`force_frame` (bool; `true` when the learner pressed "about this frame").
Response: the same shape as `POST /chat`, plus `live_frame` — `"live"` (the
captured frame was described and used as evidence), `"document"` (the material
has no frame — pdf/pptx/image — so nothing was captured and nothing invented;
the answer says so and falls back to the document's own page/slide text), or
`null` (no frame was involved).
The checks run in a fixed order, and nothing is inferred, clamped or
substituted: (1) session exists → `404`, token valid → `401`; (2) the focused
resource must be inside the session's own grant → `403 {"detail": "this
session is not authorised for that resource"}`; (3) material with no frame →
degrade to `"document"`, no vision call; (4) `current_time_ms` validated against
the server-side duration → `422`; (5) the question must be about the current
frame (or `force_frame=true`) → `422`; (6) frame checks — empty `422`, over 8 MB
`413`, not JPEG `422`, unreadable `422`. Also `429` rate limit, `502` upstream
provider error.

## Ingestion

- `POST /courses/{course_id}/ingest` — `multipart/form-data`: `file` (required:
  video, PDF, PPTX, or a PNG/JPG/JPEG/WEBP image), `subject_id` (optional form
  field) and `mode` (optional, `full` (default) or `stream`; it affects video only).
  With `subject_id` the server derives the real course from the subject and
  auto-attaches the resource to it on success. In `stream` mode the attach happens
  right after the first window. Video is processed in 120 s windows. `full` runs
  them all in one job. `stream` runs the first 3 and leaves the rest to `/position`.
  `200` → `{"job_id": "job_…", "resource_id": "res_…", "status": "pending"}`.
  Errors: `422` unsupported file type, empty file, or `mode` other than
  `full`/`stream` · `413` upload over the size cap (512 MB hard limit, or
  `MAX_UPLOAD_MB` when set) · `413` video longer than `MAX_VIDEO_MINUTES` (read
  from the container header before any model call is spent) · `429` more than
  `MAX_UPLOADS_PER_HOUR` uploads in the last hour (global, not per IP) · `404`
  unknown `subject_id` · `422` subject is not linked to a course.

  ```bash
  curl -s -X POST "$BASE/courses/demo/ingest" \
    -F "file=@test-data/Partial-derivatives.mp4;type=video/mp4" \
    -F "subject_id=sub_xxxxxxxxxxxxxxxx" -F "mode=stream"
  # {"job_id":"job_…","resource_id":"res_…","status":"pending"}
  ```
- `POST /courses/{course_id}/ingest-youtube` — JSON body `url` (required) and
  `subject_id` (optional). Same subject rules, same response envelope. Errors:
  `422` "not a recognisable YouTube URL" · `404` unknown `subject_id` · `422`
  subject is not linked to a course.
- `GET /ingestion/jobs/{job_id}` — `200` job envelope. `job_id`, `resource_id`
  and `status` (`pending` · `processing` · `done` · `failed`) are always
  present; the rest appear only when the ingest produced them: `error`,
  `stage`, `embedded`, `embedding_total`, `warnings`, `image_no`,
  `visual_status`, `visual_coverage_pct`, `visual_span_pct`, `document_status`,
  `text_chars`, `ocr_status`, `ocr_reason`, and for windowed video `mode`,
  `processed_through_ms`, `visual_through_ms`, `duration_ms`, `windows_done`,
  `windows_total`, `transcript_windows_done`, `visual_windows_done`.
  Errors: `404 {"detail": "unknown job …"}`.

  ```json
  {"job_id": "job_…", "resource_id": "res_…", "status": "processing", "mode": "stream",
   "processed_through_ms": 240000, "visual_through_ms": 120000, "duration_ms": 655500,
   "windows_done": 2, "windows_total": 6}
  ```
- `GET /courses/{course_id}/resources` — `200` → `{"resources": [{"id", "type",
  "provider", "youtube_id", "title", "status", "asset_name"}]}`; `provider` is
  `youtube` or `file`, `asset_name` is the stored asset the media route serves
  when one exists. A partly processed video also carries `processed_through_ms`
  and `visual_through_ms`, and its `status` reads as partial until both reach
  the duration. A failed listing returns an empty list, not a `500`.
- `POST /courses/{course_id}/resources/{resource_id}/position` — the player's
  playhead heartbeat for stream-mode videos. Body: `{"t_ms": 300000}` (int ≥ 0).
  The API derives both frontiers and the duration from chunk metadata, so this
  works after a restart. It queues up to 2 transcript windows while
  `frontier < t_ms + 240000`, plus a visual catch-up job while
  `visual_through_ms < processed_through_ms`. `200`:

  ```json
  {"status": "queued", "job_ids": ["job_…"], "job_id": "job_…",
   "processed_through_ms": 360000, "visual_through_ms": 240000, "duration_ms": 655500}
  ```

  `status` is `queued` (new work queued), `running` (work already in flight),
  `idle` (nothing to do yet), `ready` (both frontiers reached the end, or the
  resource is not windowed), or `failed`. `failed` comes with an `error` that
  carries the quota message while a spent quota blocks new work.
  Errors: `404` unknown resource in that course · `409 {"detail": "cannot
  process more of res_…: its media file is not on disk"}` · `422` negative
  `t_ms`.
- `DELETE /courses/{course_id}/resources/{resource_id}` — the course in the path
  must own the resource, otherwise `404` and nothing is touched. The resource is
  detached from every subject first, then its chunks (matched on the exact
  resource id) and stored file are removed. `200`:

  ```json
  {"resource_id": "res_…", "deleted": true, "detached_from": ["sub_…"], "asset_removed": true}
  ```

  Errors: `404 {"detail": "unknown resource … in course …"}` · `500` chunks
  removed but the stored file could not be deleted.
- `GET /resources/{resource_id}/media` — streams the stored asset. Locally it is
  served from disk with HTTP Range support (`206 Partial Content` when ranged).
  The hosted deployment serves it the same way. Errors: `404 {"detail": "no stored media for …"}`.

## Subjects

A subject groups existing resources. It owns references, not media: deleting a
subject removes the grouping only, and the indexed resources stay in the
retrieval registry. Subject response: `{"id", "name", "description",
"course_id", "resource_count", "resource_ids", "created_at"}`.
- `POST /subjects` → `201` subject. Body: `name` (required, 1–200 chars),
  `description` (optional, ≤2000), `course_id` (optional). Unknown fields → `422`.
- `GET /subjects` → `200 {"subjects": [ … ]}`.
- `GET /subjects/{subject_id}` → `200` subject · `404 {"detail": "unknown subject …"}`.
- `PATCH /subjects/{subject_id}` → `200` subject. Body: `name` and/or
  `description`, both optional · `404`.
- `DELETE /subjects/{subject_id}` → `200 {"deleted": "sub_1a2b3c"}` · `404`.
- `GET /subjects/{subject_id}/resources` → `200 {"resources": [ … ]}` in the
  same shape as the course listing · `404`.
- `POST /subjects/{subject_id}/resources` → `200` subject. Body: `resource_id`
  (required). `404` unknown subject; `422` unknown resource — a subject must
  never hold a scope entry that resolves to nothing.
- `DELETE /subjects/{subject_id}/resources/{resource_id}` → `200` subject · `404`.

## Auth and signing

**Launch signature.** `signature` = `hex(HMAC-SHA256(lms_launch_secret,
canonical))` over a newline-joined canonical payload:

```text
learner_id\ncourse_id\nresource_id\nlesson_id\nscope_csv\ncurrent_time_ms\nissued_at
```

`scope_csv` = `resource_scope` **sorted**, comma-joined. `lesson_id` is `""`
when absent. `current_time_ms` and `issued_at` are the raw integers **as sent**
— if you send epoch seconds, sign the seconds value. Comparison uses
`hmac.compare_digest`. A launch must be fresh: `now - issued_at <= 15 min` and
`issued_at - now <= 5 min` (after epoch-seconds normalisation).
**Dev backdoor.** An unsigned launch is accepted only when
`LMS_LAUNCH_SECRET=dev-secret` **and** `ALLOW_UNSIGNED_LAUNCH=true`; a
present-but-wrong signature is always rejected. Every launch failure returns
the same generic `401 {"detail": "invalid launch"}`; the specifics are logged
server-side only.
**Session token.** `session_token` = `hex(HMAC-SHA256(secret, session_id + "\n" +
learner_id))`, issued by `POST /lms/session`. Echo it back on every chat call.
Where it is read differs per route — the usual source of a confusing `401`:

| Route | Where the token is read from |
|---|---|
| `POST /chat` | the body field `session_token` **only** — the `X-Session-Token` header is not read on this route, so a header-only request gets `401` |
| `GET /sessions/{session_id}/messages` | the `?session_token=…` query parameter **or** the `X-Session-Token` header |
| `POST /chat/live-frame` | the `session_token` form field **or** the `X-Session-Token` header |
| `POST /quiz`, `POST /quiz/grade` | the body field `session_token` **or** the `X-Session-Token` header |

Sending both the body field and the header on `POST /chat` is harmless, and is
what the web client does.

## Error codes

| Code | Meaning | Endpoints | Typical cause |
|---|---|---|---|
| 401 | Unauthorized | `/lms/session`, `/chat`, `/chat/live-frame`, `GET /sessions/{id}/messages` | Missing/invalid/stale/future launch signature; missing or wrong session token (generic `invalid launch` detail) |
| 403 | Forbidden | `POST /chat/live-frame` | Focused resource outside the session's grant |
| 404 | Not found | `/chat`, `/chat/live-frame`, `GET /sessions/{id}/messages` | Unknown `session_id` |
| 404 | Not found | `GET /ingestion/jobs/{id}`, `GET /resources/{id}/media`, `/subjects…` | Unknown job, unknown resource asset, unknown subject |
| 404 | Not found | `POST …/position`, `DELETE /courses/{id}/resources/{rid}` | Resource unknown or not owned by that course |
| 409 | Conflict | `POST …/position` | More windows needed but the media file is gone |
| 413 | Payload too large | `POST /courses/{id}/ingest`, `POST /chat/live-frame` | Upload over 512 MB or `MAX_UPLOAD_MB`; video longer than `MAX_VIDEO_MINUTES`; captured frame over 8 MB |
| 422 | Validation error | most write routes | Empty/oversize fields, out-of-range `current_time_ms`, unknown mode or field, ingest `mode` not `full`/`stream`, unrecognisable YouTube URL, frame time outside the media duration, question not about the current frame |
| 429 | Too many requests | `/lms/session`, `/chat`, `/chat/live-frame`; `POST /courses/{id}/ingest` | Over 60 req/min/IP; over `MAX_UPLOADS_PER_HOUR` uploads (global) |
| 500 | Server error | `DELETE /courses/{id}/resources/{rid}` | Chunks removed but the stored file could not be deleted |
| 502 | Upstream error | `/chat`, `/chat/live-frame` | LLM/vision provider failure (redacted detail) |
| 503 | Service unavailable | any route that calls Gemini | A daily provider quota is spent: `{"detail": "… quota reached … Stopped instead of waiting."}` (`QuotaExhaustedError`) |

## curl smoke test

Requires the API running on `http://localhost:8123` with `LMS_LAUNCH_SECRET=dev-secret`
and `ALLOW_UNSIGNED_LAUNCH=true` (local dev only), so the launch below is unsigned.
Against the hosted API (`https://malai-pioneer-api.onrender.com`), the launch must be signed as described under
**Auth and signing**. `/health`, the subject routes and the ingest routes work
there unchanged.

```bash
BASE=http://localhost:8123
curl -s "$BASE/health"          # {"status":"ok"}

# open a session (issued_at = now, in epoch ms)
curl -s -X POST "$BASE/lms/session" -H 'content-type: application/json' \
  -d '{"learner_id":"u1","course_id":"c1","resource_id":"v1","resource_scope":["v1"],"current_time_ms":60000,"issued_at":'$(($(date +%s)*1000))'}'
# -> {"session_id":"ses_…","session_token":"…","learner_id":"u1","course_id":"c1"}
SID=ses_xxxxxxxxxxxxxxxx
TOK=xxxxxxxxxxxxxxxx

# ask a question. On POST /chat the token MUST be in the body field.
curl -s -X POST "$BASE/chat" -H 'content-type: application/json' \
  -H "X-Session-Token: $TOK" \
  -d '{"session_id":"'"$SID"'","session_token":"'"$TOK"'","current_time_ms":60000,"message":"What is the lecturer explaining right now?","mode":"qa"}'

# history (query parameter or header both work here)
curl -s "$BASE/sessions/$SID/messages" -H "X-Session-Token: $TOK"

# subjects, and an ingest to poll
curl -s "$BASE/subjects"
curl -s -X POST "$BASE/courses/c1/ingest-youtube" -H 'content-type: application/json' \
  -d '{"url":"https://www.youtube.com/watch?v=dQw4w9WgXcQ"}'
curl -s "$BASE/ingestion/jobs/job_xxxxxxxxxxxx"
```
