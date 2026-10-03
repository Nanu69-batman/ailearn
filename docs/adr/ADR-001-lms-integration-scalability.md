# ADR-001: LMS Integration & Scalability

Status: Proposed
Date: 2026-10-03

## Context

The hackathon brief requires the system to be "designed for integration with an existing LMS" and to be "scalable for concurrent learners." These are judging criteria, not future work. The service must sit alongside an existing LMS without requiring the LMS to be rebuilt. All retrieval must remain scoped by course, learner/resource permissions, and current timestamp.

## Decision

1. **Modular sidecar service.** The AI Learning Assistant is a standalone service (Next.js frontend or embeddable widget + FastAPI backend) that an LMS integrates with via a defined boundary API — not a plugin inside the LMS process.
2. **LMS integration boundary API** (`POST /lms/context` or per-request context fields): the LMS supplies, per request or per session:
   - `learner_id` (opaque, verified via shared secret / LTI-style launch token or HMAC-signed launch)
   - `course_id`, `resource_id` (video/PDF/PPT), `lesson_id`
   - `permissions` / `resource_scope` (which resources this learner may access)
   - `current_time_ms` (player position, reported by the embedded player)
   - optional `session_id` continuation
   The service never trusts client-supplied role claims without verification; identity is established at session start and carried server-side or in a signed token.
3. **Stateless API layer.** No learner/session state is held in server memory. Sessions, messages, and timestamps live in Postgres (shared persistence). Any API replica can serve any request → horizontal scaling behind a load balancer.
4. **Asynchronous background jobs.** Ingestion (transcription, PDF/PPT parsing, embedding, indexing) and any expensive recomputes run as background jobs (worker process + `ingestion_jobs` table; queue via Redis or Postgres-based job table for the prototype). The API only enqueues and reports status; concurrent learners are never blocked by ingestion.
5. **Scoped retrieval.** Every retrieval query enforces `course_id = ? AND learner_scope matches AND (source_type NOT IN ('video','video_transcript') OR (end_ms IS NOT NULL AND end_ms <= T))`. Permissions are resolved server-side from the LMS-provided scope, not from the LLM. The gate is on `end_ms` so overlapping (partially-unwatched) chunks are excluded; NULL `end_ms` video chunks are excluded and logged.
6. **PostgreSQL + pgvector for the prototype**, hidden behind a repository interface (`VectorStore` protocol). Migration path to Qdrant/OpenSearch/Weaviate documented trigger conditions below.

## Alternatives Considered

- **In-process LMS plugin / LMS rebuild:** rejected — coupling, no standalone deployment, fails the "sit alongside" requirement.
- **Dedicated vector DB (Qdrant/pgvector-excluded) now:** rejected for the prototype — second always-on service without measured need.
- **Server-held session state / sticky sessions:** rejected — prevents horizontal scaling and restart resilience.
- **Synchronous ingestion inside API requests:** rejected — ingestion takes minutes; blocks workers and learners.

## Trade-offs

- pgvector co-located with relational data means one service to run, but vector workloads (HNSW search) share CPU/IO with transactional writes; at high concurrency this becomes the scaling boundary.
- Postgres-based job queue is simpler than Redis/Celery at prototype scale, but weaker throughput; acceptable for a single-course demo workload.
- One LMS integration boundary means one contract to maintain; supporting every LMS's native API is out of scope — we define one contract and provide a thin adapter example only if time allows.

## Criteria & Validation

- **Retrieval quality:** scoped retrieval must not change recall for permitted sources. Validate: golden queries scoped to a learner with partial permissions still return correct chunks; zero chunks outside the learner's scope.
- **Grounding:** permissions reasoning unchanged; citation validation still enforced. Validate: same eval gates (leakage 0, refusal ≥ 95%) under multi-learner scopes.
- **Latency:** p50/p95 measured per stage under load (see Validation Plan §13). No latency claim until measured.
- **Scalability:** load test with concurrent learners (e.g., 25/50/100 simulated users issuing questions) against the stateless API; record throughput and p95. DB pool sizing (PgBouncer or SQLAlchemy pool limits) verified. State that scale-out is limited by the Postgres instance and define trigger points (e.g., sustained p95 > budget, vector query CPU saturation) for migrating the `VectorStore` implementation to a specialized backend.
- **Cost:** connection pooling, small top-k, and caching of repeated retrievals (by `(resource_id, T bucket, query hash)` with short TTL) reduce per-query spend; actual numbers measured, not asserted.

## Scaling Boundary (explicit)

The prototype assumes one Postgres instance (Neon/RDS small) and a handful of concurrent learners. Beyond that: add API replicas (stateless), a pooler, read replicas for session reads, and swap the vector backend only when measured p95/CPU justifies it. The `VectorStore` repository interface (`search(course_id, scope, T, query_vec, k)`) is the migration seam.
