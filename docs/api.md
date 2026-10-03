# API Reference — AI Learning Assistant MVP

Base URL (local): `http://localhost:8000`. All JSON. Auth: HMAC-signed LMS launch for session creation, which returns `session_id` + `session_token` (HMAC over session+learner); `POST /chat` and history reads must present the token (`session_token` body field or `X-Session-Token` header). Unsigned launches are rejected unless `ALLOW_UNSIGNED_LAUNCH=true` AND secret is `dev-secret` (local dev only). Rate limits: 60 req/min/IP on `/chat` and `/lms/session`.

Conventions: `current_time_ms` is player position in **milliseconds** (`0 .. 86_400_000`). Timestamps `issued_at` accept epoch **ms** (epoch seconds auto-converted server-side). Errors use FastAPI shape: `{"detail": "<msg>"}`.

## POST /lms/session

Establish a session from LMS launch context. Verifies the HMAC signature, then mints `session_id` + `session_token`.

Signature: `hex(HMAC-SHA256(lms_launch_secret, canonical))` where canonical is newline-joined:

```text
learner_id\ncourse_id\nresource_id\nlesson_id\nscope_csv\ncurrent_time_ms\nissued_at
```

`scope_csv` = `resource_scope` sorted, comma-joined; empty `lesson_id` = `""`. Launch must be fresh: `now - issued_at <= 15min`, `issued_at - now <= 5min`. Dev backdoor: unsigned launch accepted only when `LMS_LAUNCH_SECRET=dev-secret` AND `ALLOW_UNSIGNED_LAUNCH=true` (local dev only; server logs error-level). All 401s return generic `{"detail": "invalid launch"}` (specifics stay server-side).

Request:

```json
{
  "learner_id": "learner_42",
  "course_id": "course_intro_ml",
  "resource_id": "vid_lecture_03",
  "lesson_id": "lesson_03a",
  "resource_scope": ["vid_lecture_01", "vid_lecture_02", "vid_lecture_03", "pdf_notes_03"],
  "current_time_ms": 185000,
  "issued_at": 1759468800000,
  "signature": "9f2a…c1"
}
```

Success `200`:

```json
{
  "session_id": "ses_9f3ac41d02b744e1",
  "session_token": "tok_…",
  "learner_id": "learner_42",
  "course_id": "course_intro_ml"
}
```

Errors: `401 {"detail": "invalid launch"}` · `422` validation failure (empty scope, negative `current_time_ms`, unknown extra fields — `extra="forbid"`).

## POST /chat

Main grounded Q&A. Body: `{session_id, session_token?, video_id?, current_time_ms, message, mode}` (token also accepted as `X-Session-Token` header). `current_time_ms` is clamped server-side to `[0, 6h]`. Retrieval is scoped server-side from the stored session (`course_id` + `resource_scope` + `end_ms <= T` gate); history's last 5 user turns are used only for coreference rewrite — they never bypass filters. `summarize`/`quiz` additionally restrict to chunks with `end_ms <= T`. Non-refusal answers with zero citations are refused.

Modes: `qa` (default) · `explain_simple` · `analogy` · `summarize` · `compare` · `quiz`.

Request:

```json
{
  "session_id": "ses_9f3ac41d02b744e1",
  "session_token": "tok_…",
  "video_id": "vid_lecture_03",
  "current_time_ms": 185000,
  "message": "Why does the loss spike right here?",
  "mode": "qa"
}
```

Success `200` (answered):

```json
{
  "answer": "The spike comes from the learning-rate warmup step [chunk_id=ch_7f2a]. …",
  "citations": [
    {
      "chunk_id": "ch_7f2a",
      "source_type": "video_transcript",
      "video_id": "vid_lecture_03",
      "start_ms": 150000,
      "end_ms": 180000,
      "page_no": null,
      "slide_no": null
    }
  ],
  "refused": false
}
```

Success `200` (refused — no in-scope evidence at/below T, or cited ID outside retrieved set):

```json
{
  "answer": "I don't have enough information in the materials covered so far to answer that.",
  "citations": [],
  "refused": true
}
```

Quiz example (`"mode": "quiz"`):

```json
{
  "session_id": "ses_9f3ac41d02b744e1",
  "current_time_ms": 185000,
  "message": "Quiz me on what I have watched so far",
  "mode": "quiz"
}
```

→ 2–3 Q/A pairs grounded in chunks with `start_ms <= 185000`, each Q/A citing its chunk.

Errors: `404 {"detail": "session not found"}` (unknown `session_id`) · `422` (empty message, message > 4000 chars, bad mode, negative T, extra fields).

## GET /sessions/{id}/messages

Conversation history in insertion order (user + assistant turns; assistant turns carry validated citations).

Success `200`:

```json
{
  "session_id": "ses_9f3ac41d02b744e1",
  "messages": [
    {
      "role": "user",
      "content": "Why does the loss spike right here?",
      "citations": [],
      "created_at": "2026-10-03T10:01:00.123456+00:00"
    },
    {
      "role": "assistant",
      "content": "The spike comes from … [chunk_id=ch_7f2a].",
      "citations": [
        {
          "chunk_id": "ch_7f2a",
          "source_type": "video_transcript",
          "video_id": "vid_lecture_03",
          "start_ms": 150000,
          "end_ms": 180000,
          "page_no": null,
          "slide_no": null
        }
      ],
      "created_at": "2026-10-03T10:01:02.456789+00:00"
    }
  ]
}
```

Errors: `404 {"detail": "session not found"}`.

## GET /health · GET /health/db

Liveness: `GET /health → 200 {"status": "ok"}` (no deps; used by Docker `HEALTHCHECK` and Render health checks). Readiness: `GET /health/db → 200 {"status": "not_implemented", "database_url_set": true|false}` — real DB ping lands when the SQLAlchemy engine is wired; treat `"not_implemented"` as "API up, DB check pending".

## Error code summary

| Code | Meaning | Endpoints | Typical cause |
|------|---------|-----------|---------------|
| 401 | Unauthorized | `POST /lms/session` | missing/invalid/stale/future launch signature (generic `invalid launch` detail) |
| 404 | Not found | `POST /chat`, `GET /sessions/{id}/messages` | unknown `session_id` |
| 422 | Validation error | `POST /lms/session`, `POST /chat` | empty fields, out-of-range T, oversize message, unknown mode, extra keys |

`curl` smoke test:

```bash
curl -s http://localhost:8000/health
curl -s -X POST http://localhost:8000/lms/session \
  -H 'content-type: application/json' \
  -d '{"learner_id":"u1","course_id":"c1","resource_id":"v1","resource_scope":["v1"],"current_time_ms":60000,"issued_at":'$(($(date +%s)*1000))'}'
```
