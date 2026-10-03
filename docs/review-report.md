# Cross-System Code Review — AI Learning Assistant MVP
**Scope:** `apps/api/app` (ingestion, retrieval, chat, eval, middleware, config, main) + `apps/api/tests` + `apps/web` (contract conformance vs `apps/api/app/chat/schemas.py`, `apps/api/app/chat/router.py`).
**Read first:** `docs/IMPLEMENTATION_PLAN.md`, `docs/adr/ADR-001-lms-integration-scalability.md`, `.opencode/instructions/INSTRUCTIONS.md` (quality gates).
**Mode:** report only — no code changed.

## Verdict
**NOT merge-ready as an integrated system.** Backend timestamp gating (SQL / in-memory / post-retrieval assertion) is consistent and well-tested in isolation, but the **frontend cannot successfully talk to the backend** (3 independent contract breaks on the two calls the UI makes), the **production wiring is dead** (stub LLM + in-memory store/sessions are the live path), and the **strict `end_ms` rule is undermined in exactly two places** (chat strict-mode filter, eval detector). Auth defaults to fail-open.

## Findings by severity

### CRITICAL (must fix before any integrated demo counts as passing)

**C1 — ChatMode vocabulary mismatch: every default chat send 422s.**
- `apps/web/lib/api.ts:13-19` — `ChatMode` includes `"ask"`, omits `"qa"`.
- `apps/web/components/ChatPanel.tsx:36` — default mode `"ask"`.
- `apps/api/app/chat/schemas.py:11-17` — `ChatMode` enum has `qa`, no `ask`; `ChatRequest.mode` validated with `extra="forbid"` (`schemas.py:40`).
- Any `POST /chat` with `"mode":"ask"` → 422. Frontend tests would never pass against the live API.
- **Fix:** rename frontend `"ask"` → `"qa"` (label can stay "Ask") in `api.ts` + `fixtures.ts` (`MOCK_ANSWERS.ask`, `mockChatAnswer` fallback `?? MOCK_ANSWERS.ask`), or add a mode map in `postChat`. Add a contract test asserting `Object.keys(MOCK_ANSWERS)` ⊆ backend enum values.

**C2 — `getMessages` response-shape mismatch: history load breaks at runtime.**
- Backend returns an envelope `apps/api/app/chat/router.py:93-107` → `MessagesResponse{session_id, messages[]}` (`schemas.py:72-74`).
- Frontend `apps/web/lib/api.ts:164-168` types `getMessages()` as `Promise<ChatMessage[]>` and `ChatPanel.tsx:54` does `setMessages(history)` with the envelope object → `.map` crash in `MessageList`, or silent empty state.
- **Fix:** unwrap in `getMessages` (`return (await apiFetch<{messages: ChatMessage[]}>(...)).messages`) and type the envelope. Add a fetch-mock test for the unwrap.

**C3 — Session bootstrap can never succeed against a real backend (two independent breaks).**
- (a) `issued_at` type: backend `LmsSessionRequest.issued_at: int` epoch ms/seconds (`schemas.py:30`, `launch.py:62-67`); frontend sends ISO string `apps/web/lib/fixtures.ts:22` (`issued_at: new Date().toISOString()`) → 422 always.
- (b) `signature: "dev"` (`fixtures.ts:21`): `verify_lms_launch` (`launch.py:93-97`) only bypasses auth when signature is **absent** AND secret is dev; a present-but-wrong signature (`"dev"`) → `invalid signature` 401 even in dev mode. So bootstrap 401s when API is configured; the app only ever works offline.
- **Fix:** dev fixture should omit `signature` (make it optional in `LmsSessionRequest` TS type) and send `issued_at: Date.now()` (number). Document that prod LMS must sign via `sign_launch` canonical payload (`launch.py:22-42`).

**C4 — Fail-open auth default: unsigned launches accepted unless env is set.**
- `apps/api/app/config.py:11` — `lms_launch_secret: str = "dev-secret"`; `launch.py:94-96` + `router.py:64-65` accept missing signatures in dev-secret mode with only a `log.warning`.
- A deploy that forgets `LMS_LAUNCH_SECRET` silently disables launch auth — violates ADR-001 "never trusts client claims" and the INSTRUCTIONS auth gate.
- **Fix:** require explicit opt-in, e.g. `ALLOW_UNSIGNED_LAUNCH=false` default; refuse to start (or refuse unsigned) when secret equals `DEV_SECRET` in `ENV=production`; make the warning an error-level log with request id.

### HIGH (should fix before merge / before any "measured" claim)

**H1 — `apply_strict_timestamp` enforces the wrong predicate (`start_ms`, not `end_ms`).**
- `apps/api/app/chat/service.py:39-48`: keeps passages with `p.start_ms is None or start_ms <= T` for `summarize`/`quiz`.
- Spec everywhere else is `end_ms <= T` (plan §6, ADR-001 §5, `store.py:49-52`, `filters.py:61-67`). Consequences: (1) a video chunk with `start_ms=None` (or any chunk that slipped the store gate with `end_ms > T`) is **kept**; (2) non-transcript chunks carrying `start_ms` get incorrectly time-gated though spec says T does not apply to them. Today the store gate usually runs first so this is latent, but the defense-in-depth layer is checking the wrong thing, and any caller that reuses `apply_strict_timestamp` without the store gate leaks.
- **Fix:** mirror `filters.assert_no_leakage` semantics: keep video/`video_transcript` only if `end_ms is not None and end_ms <= T`; keep all other source types unconditionally; log drops. Reuse one shared predicate instead of two implementations.

**H2 — Eval harness is blind to the NULL-`end_ms` leakage class (detector + cutoff disagree with production).**
- `apps/api/app/eval/harness.py:95-99` (`detect_leakage`): only flags `end_ms is not None and end_ms > T` → a NULL-`end_ms` video chunk in the retrieved set scores as clean.
- `harness.py:130-137` (`apply_strict_cutoff`): **keeps** NULL-`end_ms` video chunks when `start_ms <= T`, while production (`store.py:98-105`, `filters.py:94-108`) **drops** them. The pre-rank pool and the "production_gap" diagnostic (`harness.py:302`, which reuses the blind detector) can report 0 while disagreeing with production on this class.
- **Fix:** flag NULL-`end_ms` video/`video_transcript` in `detect_leakage` (reason `timestamp`, detail `end_ms=NULL`); drop them in `apply_strict_cutoff`. Add one golden/test case with a NULL-`end_ms` video chunk asserting leakage is flagged.

**H3 — `/chat` + message history are unauthenticated bearer-ID endpoints with client-controlled T and no rate limiting.**
- `router.py:79-107`: no auth dependency, no learner binding, no check that `current_time_ms` matches (or is bounded by) the signed launch `current_time_ms` (`router.py:45-73` stores it but never re-checks). Anyone holding a `session_id` (64-bit `ses_` hex, `sessions.py:55` — unguessable but bearer) can query **any T** (set `T=end` → spoiler unlock, by design the server trusts the player clock) and read full history (`GET /sessions/{id}/messages` — no owner check → IDOR-shaped exposure if IDs ever leak via logs/proxies). No rate limiting anywhere (INSTRUCTIONS security gate requires it).
- **Fix (MVP-minimal):** sign or bind `session_id` (e.g. return an HMAC token over session id + learner at `/lms/session`, verify per request), clamp `current_time_ms` to a server-known video duration ceiling, add slowapi/Redis rate limits on `/chat` + `/lms/session`, and never log `session_id` at info level.

**H4 — Production wiring is dead: live traffic would get stub answers from an empty/ephemeral store.**
- `chat/llm.py:140` `get_llm_provider()` is **never called** in app code (only tests reference stores); `ChatService.__init__` defaults to `DeterministicStubLLM()` (`service.py:64`); `RetrievalService` defaults to `InMemoryVectorStore([])` + `HashingEmbedder` (`retrieval/service.py:55-62`); `PostgresVectorStore` is never instantiated outside tests; sessions/messages are process-local singletons (`chat/sessions.py:103-104`) — contradicts plan §1/§3 and ADR-001 decision 3 (stateless API, Postgres-shared state). A deployed instance serves stub text, loses all sessions on restart, and cannot scale past 1 replica.
- **Fix:** wire factory in `router.get_service` (settings-driven: `get_llm_provider()`, `PostgresVectorStore` when `database_url` is real, Postgres-backed session/message repos behind the same method names); keep in-memory only under an explicit `USE_IN_MEMORY=true` test/dev flag; add a startup log line stating which providers are active.

**H5 — Uncited non-refusal answers pass validation (only *unknown* IDs refuse).**
- `service.py:106-125`: `unknown = cited not in retrieved` → refuse; but `cited_ids == []` with a substantive answer returns `refused=False, citations=[]`. "Cite every factual claim" (`prompts.py:30-38`) and plan §6 ("forces refusal" when evidence insufficient) are unenforced for the silent-omission case; a real LLM that answers without citing sails through.
- **Fix:** if mode expects grounding and `result.text != REFUSAL_TEXT` but `cited_ids` is empty → refuse (or single-retry the LLM with a "cite your claims" repair prompt, then refuse). Exempt nothing silently; log `uncited_answer` warnings.

**H6 — Half the plan §3 API surface is missing; `/health/db` is a stub.**
- No routes for `POST /courses/{id}/ingest`, `GET /ingestion/jobs/{id}`, `GET /courses/{id}/resources`, `POST /sessions` (only `/lms/session`, `/chat`, `/sessions/{id}/messages` exist). `ingestion/jobs.py:136-156` can update job rows but nothing enqueues them. `main.py:31-34` returns `not_implemented` for DB readiness — readiness probes cannot detect DB outage.
- **Fix:** either implement the enqueue/status/resources routes (thin, over the existing `run_ingestion` + `ingestion_jobs` table) or explicitly mark them out-of-MVP in plan §3; implement a real `SELECT 1` DB ping for `/health/db`.

**H7 — Dev fixture scope vocabulary (`["video","pdf","pptx"]`) matches no real resource ID.**
- `fixtures.ts:19` sends type names as `resource_scope`; backend scopes on **resource IDs** (`store.py:87-93`, `filters.py:69-76`, plan §6). Against real chunks (`res_video`/`res_pdf`/`res_ppt` in `eval/fixture.py:11-15`, `vid`/`handout` in `tests/test_chat.py:59-72`) this scope matches nothing → empty retrieval → refusal. Demonstrates the contract never ran end-to-end.
- **Fix:** fixture scope should be resource IDs (e.g. `["res_video","res_pdf","res_ppt"]` for the fixture course); document `resource_scope: string[] // resource IDs, not types` in both `api.ts` and backend schema description.

### MEDIUM (should fix soon)

**M1 — Citation nullability: backend optional fields vs frontend required fields → `NaN` seek crash.**
- Backend `Citation` all-optional except `chunk_id` (`schemas.py:49-56`); frontend `Citation` requires `source_type/video_id/start_ms/end_ms` (`api.ts:52-60`); `CitationBadge.tsx:22-33` calls `onSeek(citation.start_ms)` unguarded (`page.tsx:30-35` → `el.currentTime = NaN` on null).
- **Fix:** make frontend fields nullable to match backend and guard: render video badge only when `start_ms != null && end_ms != null`, else fall back to doc-style badge; add a component test with a null-timestamp citation.

**M2 — Smaller contract mismatches.**
- `ChatRequest.video_id`: required `string` frontend (`api.ts:36-42`) vs optional backend (`schemas.py:43`). Make it `video_id?: string`.
- `LmsSessionResponse`: backend returns `{session_id, learner_id, course_id}` (`router.py:74-76`); frontend types only `session_id` (`api.ts:32-34`). Extend the type (harmless, avoids future confusion).
- `LmsSessionRequest.lesson_id` required frontend vs optional backend; `issued_at` string vs int (covered in C3).

**M3 — Launch replay + oracle-flavored errors.**
- No nonce/jti: a captured signed launch replays for the full 15-min window (`launch.py:107`), minting sessions each time. Distinct 401 details (`invalid signature` / `stale launch` / `future`) give a timing/oracle surface for forging.
- **Fix:** add optional `nonce` (server-kept short-TTL seen-set, Postgres-backed per ADR statelessness) or accept replay risk explicitly; return generic `invalid launch` detail to clients while logging specifics server-side.

**M4 — `run_ingestion` silent dry-run.**
- `jobs.py:169-186`: DB-unreachable → `log.info` + returns chunks as if successful; caller cannot distinguish persisted vs dropped work; `_set_job_status` failure is warning-only (`jobs.py:156`). Ingestion "succeeds" while indexing nothing.
- **Fix:** return a result object `{chunks, persisted: bool}` or raise/return status; surface `persisted:false` to the jobs endpoint (H6); escalate job-status write failures to error log with job id.

**M5 — Unhandled-exception path loses request-id correlation and typed errors.**
- `middleware.py:16-34`: `call_next` unguarded — on raise, no `x-request-id` echo, no JSON log, and FastAPI returns HTML 500 (INSTRUCTIONS: typed API errors, no sensitive leak). `post_chat` (`router.py:79-90`) only maps `SessionNotFoundError`; retrieval/LLM exceptions bubble raw.
- **Fix:** wrap `dispatch` in try/except (log with request id, return JSON `{"detail":"internal error"}` + header); add `HTTPException` handler preserving envelope; map provider errors to 502/504 with redacted detail.

**M6 — Eval scoring heuristics are weak and untuned.**
- `DEFAULT_THRESHOLD = 0.08` (`harness.py:43`) and token-overlap `citation_supported ≥ 2` (`harness.py:167-177`) are unvalidated constants; `should_refuse` (`harness.py:180-200`) never exercises the production LLM refusal path (offline score-threshold proxy). `p95` index math (`harness.py:247-251`) is ad-hoc. Fine for a gate, but no "measured" quality claim should rest on these without sensitivity analysis.
- **Fix:** record threshold-sweep results in `docs/eval-results/`; note the proxy gap in the harness docstring.

**M7 — Frontend masks backend failures with mock answers.**
- `ChatPanel.tsx:109-122`: any `postChat` error still appends a mock assistant answer ("offline fallback" suffix) while also showing `sendError`. Combined with auto-offline bootstrap (`SessionBootstrap.tsx:27-31`), a broken backend looks half-working and bug reports lose signal.
- **Fix:** keep fallback but mark clearly (already suffixed — good) and add a visible persistent "backend unreachable, showing mock data" banner state instead of per-message fallback; log `requestId` from `formatApiError` for correlation.

### LOW (nice to have)

**L1 — `print()` in eval CLI** (`harness.py:374`) trips the "no print" gate literally, but it is the CLI's stdout output path — acceptable; prefer `sys.stdout.write` or an explicit gate exception comment.
**L2 — Hardcoded demo IDs** (`page.tsx:22` `videoId="demo-video"`, fixture `resource_id="demo-resource"`) match nothing backend-side; fine for mock mode, must be replaced by session/resource context when live (see H7).
**L3 — CORS `allow_methods/headers=["*"]` with `allow_credentials=True`** (`main.py:15-21`): acceptable single-origin prototype posture; tighten to explicit methods/headers before prod.
**L4 — `MessageOut.citations: list[Citation | dict]`** (`schemas.py:68`) weakens the response contract; prefer `list[Citation]` now that `_persist` stores `model_dump()`s (`service.py:122-124`).
**L5 — No pagination on message history** (`router.py:93-107` returns full list) — fine for MVP demo lengths; add `limit`/`cursor` when histories grow (INSTRUCTIONS pagination gate).

## What is solid (keep as-is)
- Timestamp rule is stated identically in plan §6, ADR-001 §5, SQL clause (`store.py:49-52`), in-memory predicate (`store.py:87-108`), and second-gate assertion (`filters.py:54-126`), including NULL-`end_ms` exclusion, boundary `end_ms == T` kept, docs unaffected by T, and `permitted` + course + scope checks. Tests cover overlap/edge/NULL/scope/out-of-course (`tests/test_retrieval.py`, `tests/test_chat.py`).
- Hybrid RRF + triple `assert_no_leakage` (vector, FTS, post-rerank in `hybrid.py:124-163`) means an untrusted FTS/reranker cannot smuggle post-T content in. Reranker is reorder-only by protocol.
- No path found where **unfiltered transcript reaches the LLM**: `service.chat` (`service.py:73-125`) only forwards gated `passages`; history fed to the prompt is user texts only (`sessions.py:95-97`, `prompts.py:94-97`); retrieved content is wrapped as untrusted `<context>` with anti-instruction preamble (`prompts.py:30-38`). Residual note: user-pasted future text travels in `history` as coreference context (M-side, by design — the model is instructed it is not evidence).
- Citation allowlist check (unknown-ID → refusal, `service.py:113-118`, tested in `test_chat.py:179-196`) plus answer-text re-parse (`service.py:109-111`) so providers cannot smuggle IDs past their own `cited_ids`.
- HMAC verification itself is sound: canonical payload with sorted scope (`launch.py:22-42`), `hmac.compare_digest` (`launch.py:103`), 15-min age + 5-min future skew (`launch.py:107-110`), seconds-tolerant `issued_at` (`launch.py:62-67`). Issues are fail-open default (C4) and replay (M3), not the primitive.
- Secrets posture in code is clean: env-driven (`config.py`), no hardcoded keys/tokens in app code, API keys only in headers/memory (`chat/llm.py`, `ingestion/embedder.py`), `.env.example` contains only public `NEXT_PUBLIC_*` URLs. No `console.log` in web app code; no `print` in request paths.
- Answer rendering is XSS-safe (plain `<div>` text, no `dangerouslySetInnerHTML` in `MessageList.tsx:43`).

## Recommended fix order
1. C1–C3 + H7 (frontend↔backend contract: mode, messages unwrap, `issued_at`/`signature`/scope) — unblocks any integrated run.
2. C4 (fail-closed auth default) + H3 (per-request binding, T clamp, rate limits).
3. H1 + H2 (one shared `end_ms` predicate used by service, store, filters, harness detector, cutoff).
4. H5 (refuse uncited answers) → re-run golden eval, confirm `leakage_rate == 0`, `refusal_correctness ≥ 0.95`.
5. H4 + H6 (production wiring + missing routes + real `/health/db`) before any latency/cost/load claim.

*Quality-gate note: verification commands (`pytest`, `tsc`, `ruff`) were not executed in this review pass (report-only); run `pytest apps/api/tests`, `npx tsc --noEmit` in `apps/web`, and `ruff check` on backend before merging fixes.*
