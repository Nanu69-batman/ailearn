# AI Learning Assistant — MVP Implementation Plan

Hackathon: AI-Powered Interactive Learning. Core flow: WATCH → PAUSE → ASK → UNDERSTAND.

## 1. Architecture Overview

The assistant is a **modular sidecar service** that sits alongside an existing LMS — the LMS is never rebuilt. Frontend/widget and backend are deployable/embeddable separately.

- **Frontend:** Next.js 15 (App Router, TypeScript, shadcn/ui) — video player page with embedded `<video>`, chat panel, citations rendering; embeddable in an LMS page.
- **Backend:** Python 3.12+ FastAPI — REST API, LMS boundary, session layer, retrieval, LLM orchestration. **Stateless**: no in-memory learner/session state; horizontally scalable.
- **Database:** Postgres + pgvector (Neon/RDS small for prototype). One store for relational + vectors; also the prototype job queue and shared session state.
- **Background workers:** ingestion (transcription, parsing, embedding, indexing) runs async; API only enqueues/reports status.
- **LLM:** Hosted API for chat + query rewriting; embeddings via same provider (or local BGE fallback).
- **Video asset:** Cloudflare R2 / S3 + CDN; player reports `currentTime`.

### LMS Integration Boundary

Per request (or at session creation), the LMS provides:
`learner_id` (opaque; verified via HMAC-signed launch/LTI-style token), `course_id`, `resource_id`, `lesson_id`, `resource_scope`/permissions, `current_time_ms`, optional `session_id`. The service resolves permissions server-side and scopes all retrieval. Client-supplied identity claims are never trusted without a signed token.

### Request Data Flow

`Player pause (T) → POST /chat {session context, current_time_ms, question} → verify launch token → coreference rewrite → hybrid retrieval with course+permission+T filters → rerank → grounded prompt → LLM → validate citations → {answer, citations[], refused}`.

### Prototype vs Scale-Out

**Prototype deployment:** single API instance, Neon/RDS small, Postgres JSONB + HNSW, background thread/process for jobs, few concurrent learners. **Scale-out:** N stateless API replicas behind LB, PgBouncer/pool, read replicas for history reads, dedicated workers, retrieval cache, and a specialized vector backend via the `VectorStore` repository interface once measured limits (p95 latency, CPU saturation, chunk volume) are hit. See `docs/adr/ADR-001-lms-integration-scalability.md`.

## 2. Data Model (Postgres)

- `courses(id, title)`; `resources(id, course_id, type[video|pdf|pptx|note|code], asset_uri, sha256, status)`
- `chunks(id, resource_id, course_id, source_type, video_id, start_ms, end_ms, page_no, slide_no, chunk_index, text, text_hash, embed_model vector(1536), permitted)`
  - indexes: `(course_id, start_ms)` B-tree, HNSW on `embedding`
- `sessions(id, course_id, video_id, created_at)`; `messages(id, session_id, role, content, citations jsonb, created_at)`
- `citation` denormalized into `messages.citations` (no separate table needed for MVP)
- `ingestion_jobs(resource_id, status, ingest_version, updated_at)`

## 3. API Boundaries

| Endpoint | Purpose |
|---|---|
| `POST /lms/session` | establish session from LMS launch context (learner, course, resource, scope, T) |
| `POST /courses/{id}/ingest` | enqueue async ingestion job |
| `GET /ingestion/jobs/{id}` | ingestion status |
| `GET /courses/{id}/resources` | list resources permitted for caller |
| `POST /sessions` | create session (from LMS context) |
| `POST /chat` | main: `{session_id, video_id, current_time_ms, message}` → `{answer, citations, refused}` |
| `GET /sessions/{id}/messages` | conversation history |
| `GET /health` | health check (+ `/health/db` readiness) |

Backend owns all IDs, timestamps, filters, citation validation. LLM never receives unfiltered transcript access. API layer is stateless: all session/learner state lives in Postgres so any replica can serve any request. Retrieval scoping: `course_id` + learner permission scope + `start_ms <= T` enforced in SQL, plus post-retrieval assertion.

## 4. Frontend Structure

- `app/(learn)/[courseId]/page.tsx` — video player + chat side panel
- Components: `VideoPlayer` (reports timeupdate/pause), `ChatPanel`, `MessageList`, `CitationBadge` (click → seek player to timestamp / open PDF page)
- `lib/api.ts` — typed fetch client; Zustand or React context for session state
- Loading/error/empty states mandatory per quality gate.

## 5. Ingestion Pipeline (async background jobs)

Ingestion never runs inside API requests. `POST /courses/{id}/ingest` enqueues an `ingestion_jobs` row (status: pending|processing|done|failed); a separate worker process executes it. Concurrent learners are unaffected. CLI remains for local/seed use.

Stages: extract → normalize → chunk → embed → index (see `docs/research-ingestion.md`).
- Video: faster-whisper (or parse existing `.vtt`/`.srt`); transcript anchored segments 30–60s windows, 10–15s overlap, never mid-sentence; keep raw segments for citations.
- PDF: pymupdf page-level; PPTX: python-pptx slide-level; notes/code: chardet + plain read.
- Chunk metadata as app-owned fields (see schema in research note); deterministic `chunk_id = sha1(source_id + segment_key + text_hash)`; sha256 asset skip; `ingestion_jobs` for retry; transaction per source.

## 6. RAG Pipeline & Timestamp Enforcement

- Hybrid: BM25 (Postgres FTS) + pgvector cosine → RRF fusion → cross-encoder rerank top-20 → top-5–8 (per `docs/research-rag.md`).
- **Hard enforcement at query layer:** `WHERE course_id=$1 AND learner_scope matches permitted resources AND (source_type NOT IN ('video','video_transcript') OR (end_ms IS NOT NULL AND end_ms <= $T))`; post-retrieval assertion drops any video chunk with `end_ms > T`, NULL `end_ms`, or outside the caller's scope before prompt assembly; log violations. The gate is on `end_ms` (not `start_ms`) so no retrieved chunk contains content taught after the pause point — decided after the eval harness measured a 0.48 production-gap rate under the looser `start_ms` rule with no recall loss under the strict rule (`docs/eval-results/baseline-strict.json`). Never rely on the prompt.
- Follow-ups: rewrite query with coreference using last 3–5 turns + rolling summary; history never bypasses the filter.
- Citations: LLM emits `[chunk_id=...]`; backend maps to stored metadata, validates timestamps/pages against retrieved set, else forces refusal.
- Guardrails: retrieved content wrapped as untrusted `<context>` data; refusal policy; citation allowlist validation.

## 7. Learning Features (MVP scope)

1. Contextual video Q&A at current timestamp (core)
2. Explain simply / analogy (prompt modes on same retrieval)
3. Summarize learned-so-far (retrieve all chunks ≤ T, summarize)
4. Compare concepts (two-query retrieval + synthesis)
5. Short quiz generation from covered content (≤ T only)
6. PDF/PPT-grounded answers
Mock interview = Phase 2+ extension.

## 8. Evaluation Strategy (gate: timestamp leakage == 0)

- ~25-pair golden JSONL dataset (`pause_at`, type, gold sources, expected answer|refuse), incl. edge pauses T=0 / segment boundary / T=end / adversarial "spoiler" probes.
- Metrics: retrieval accuracy@k, citation accuracy, hallucination rate, timestamp leakage rate, p50/p95 latency, $/query.
- CI gate on every chunking/embedding/prompt change: leakage 0, refusal correctness ≥95%, other metrics within 2pp of baseline. (`docs/research-evaluation.md`)

## 9. Deployment Plan

**Prototype (demo):** Vercel (frontend) + Render (FastAPI, single instance) + Neon (Postgres+pgvector); R2 for video; env via dashboards + pydantic-settings; `/health`, JSON logs, request-id middleware.
**Scale-out (documented, gated on measured load):** N stateless API replicas + LB, PgBouncer, read replicas, dedicated worker service for ingestion, retrieval cache, and a specialized vector backend via the `VectorStore` interface. Full details and triggers: `docs/adr/ADR-001-lms-integration-scalability.md`.
- CI: GitHub Actions — FE lint/build, BE ruff/pytest/Docker smoke.
- AWS path documented (Amplify + App Runner + RDS Serverless + S3/CloudFront) but not primary for demo.
- Demo checklist: seeded DB, embedded transcript, CDN video reachable, `/health` 200, CORS set, LLM key funded, fallback recorded demo. (`docs/research-deployment.md`)

## 10. Execution Phases

0. ADR-001 review (LMS boundary + scalability) — no code until boundaries are approved.
1. **Scaffold**: repo layout (`apps/web`, `apps/api`), env, CI skeleton, DB schema migration.
2. **Ingestion**: transcript/PDF/PPT extractors + chunk/embed/index; seed sample course.
3. **Retrieval core**: repository layer, hybrid search, timestamp filter, assertion gate.
4. **Chat API**: sessions, query rewrite, grounded generation, citation validation.
5. **Frontend**: player + chat + citations click-to-seek.
6. **Eval**: golden dataset + leakage tests wired into CI.
7. **Deploy**: Vercel/Render/Neon + docs (README, API docs, architecture diagram).
8. **Stretch**: explain-simply/analogy modes, summarize, quiz, then mock interview extension.

## 11. Architecture Justification

All claims below are **hypotheses**. None of these choices has been benchmarked in this project yet. Each section states the rationale, then how we will validate it during implementation. No performance, scalability, accuracy, or cost claim should be presented to judges as a measured fact until its validation method has been run and results recorded in `docs/eval-results/`.

### Retrieval Quality
**Hypothesis:** Hybrid retrieval (BM25 + dense vectors, RRF fusion, cross-encoder rerank) will surface more relevant chunks than either retriever alone, because keyword matching catches exact terms (names, formulas) that embeddings miss, and embeddings catch paraphrase that keywords miss.
**Validation:** Build the golden dataset (§8) and measure retrieval accuracy@k for three configurations — dense-only, BM25-only, hybrid+rerank — on the same queries. Record hit-rate@5/@10 per config; adopt the winner as default.

### Grounding
**Hypothesis:** Forcing the LLM to cite by `chunk_id`, validating every citation against the retrieved set, and refusing when evidence is insufficient will keep fabricated citations and unsupported answers near zero.
**Validation:** Run the citation-support check from the golden set (each cited timestamp/page/slide must contain the claimed content). Track citation accuracy and hallucination rate across eval runs; any fabricated citation is a release blocker.

### Latency
**Hypothesis:** Filtering by metadata (course, timestamp) before reranking and sending only top-5–8 chunks to the LLM will keep p95 response time within a demo-acceptable range compared to naive "send the whole transcript" approaches.
**Validation:** Instrument the chat endpoint with latency logging (retrieval, rerank, LLM, total). Measure p50/p95 over a seeded question set at demo scale. Report per-stage breakdowns; do not claim speedups without a measured baseline.

### Scalability
**Hypothesis:** Postgres + pgvector with HNSW and B-tree indexes will comfortably serve the demo and near-term pilot load with a single database, avoiding the operational cost of a second vector service.
**Validation:** Load-test the retrieval query path with increasing chunk counts (1k → 50k) and concurrent learners. Defer any move to Qdrant/OpenSearch until measured numbers show the limit; the repository interface exists only to make that swap possible, not because it is already needed.

### Cost
**Hypothesis:** One managed Postgres (Neon/RDS), a small embedding model, bounded top-k, and cheap one-off ingestion will keep demo operating cost low.
**Validation:** Log token usage per query (embedding + LLM) and track per-query dollar cost in eval runs. No cost figure should be quoted to judges until we have an actual per-query measurement and projected monthly estimate documented in `docs/cost-estimate.md`.

### Trade-offs
- pgvector is less specialized than Qdrant/OpenSearch/Weaviate at very large scale, and has weaker managed-filtering ergonomics — accepted to avoid running a second always-on service.
- Cross-encoder reranking adds one model call per query — accepted for grounding quality, to be revisited if latency budget fails validation.
- Single-provider hosted LLM/embeddings for the demo — accepted for speed; interface seam allows swapping providers.
- AWS described as a documented target path, not the demo deployment — accepted to prioritize shipping a working hosted demo over AWS service depth.

### Validation Plan
1. Golden dataset v1 (~25 pairs) checked in before frontend work begins.
2. Every retrieval/prompt/chunking change reruns the eval script; scoreboard committed.
3. CI gate: timestamp leakage == 0, refusal correctness ≥ 95%, no metric regression > 2pp.
4. Measured results (latency, cost, hit-rate, citation accuracy) recorded in `docs/eval-results/` and referenced from README — hypotheses never presented as results.

## 13. Production Expectations Checklist (brief §7)

| Requirement (problem statement) | Where addressed |
|---|---|
| Hosted and publicly accessible | §9 prototype deployment (Vercel/Render/Neon), no localhost-only submission |
| API-driven and modular | §3 API boundaries; sidecar service in §1 |
| Secure and documented | Signed LMS launch token (ADR-001), server-side permission resolution, `.env` secrets, README + API docs + architecture diagram as deliverables |
| Scalable for concurrent learners | §1 scale-out split, §13 load-test plan, ADR-001 |
| Designed for integration with an existing LMS | §1 LMS integration boundary, `POST /lms/session`, ADR-001 |
| Clean configuration, logging, error handling | pydantic-settings validation at startup, structured JSON logs, request-id middleware, typed API errors |
| Not a simple chatbot connected directly to an LLM API | Retrieval-first architecture with timestamp enforcement, citation validation, and evaluation gates (§6, §8) |

## 14. Scalability Validation Plan

All numbers below are targets to be measured, not results.

1. **Load test:** k6/Locust scenarios with 25, 50, 100 simulated concurrent learners issuing `/chat` with varying `current_time_ms`. Record requests/sec, error rate, p50/p95/p99 latency end-to-end and per-stage (auth, retrieval, rerank, LLM, validation).
2. **Connection management:** verify SQLAlchemy pool limits and/or PgBouncer hold up at target concurrency; watch for pool exhaustion errors.
3. **Caching:** measure hit rate and latency delta with a short-TTL retrieval cache keyed on `(resource_id, T bucket, query-embedding hash)`; only keep if measured p95 improves.
4. **Background jobs:** run ingestion of a full course while learners query; API p95 must not degrade beyond the measured budget and jobs must complete with correct status transitions.
5. **Vector retrieval:** measure search latency as chunk count grows (1k → 50k). This sets the documented scaling boundary for pgvector vs migrating the `VectorStore` implementation to Qdrant/OpenSearch.
6. **Prototype vs scale-out gate:** only after sustained measured load do we add API replicas, read replicas, or a specialized vector backend; each addition recorded in an ADR update.

## 12. Delegation Map (Agent Kit)

| Work | Agent |
|---|---|
| Backend, ingestion, RAG, LLM, eval | `@python`, `@learning-ingestion-engineer`, `@learning-rag-engineer`, `@learning-llm-engineer`, `@learning-evaluation-engineer` |
| Schema, pgvector indexes | `@database` |
| Frontend | `@frontend-react` (+ `@designer` for Design phase) |
| Deployment/CI | `@devops` |
| QA gate | `@reviewer` |
