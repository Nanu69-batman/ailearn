# Persistence Architecture Research — AI Learning Assistant

## Workload
Users/sessions/conversations (relational), videos/resources/courses (relational),
transcript chunks + embeddings with metadata filters (`timestamp <= T`, `course_id`,
`resource_ids[]`), citations (chunk → source spans).

## Options Compared

| Option | Metadata filters | Timestamp range | Cost | Ops | AWS fit |
|---|---|---|---|---|---|
| (a) Postgres + pgvector | Excellent — native SQL, JSONB, arrays, `IN/ANY`, indexes | Native `BETWEEN`/`<=` | Cheapest for small scale | Lowest — one DB, Prisma/SQLAlchemy, RDS manages backups | RDS Postgres / Aurora, `CREATE EXTENSION vector` |
| (b) ChromaDB | Good (where clauses) but embedded/single-node | Supported via operators | Free | Easy local, poor hosted prod story | No managed offering; self-host Docker only |
| (c) Qdrant | Strong payload filters, must/should conditions | Range filter native | Free tier small | Easy Docker; one more service | No first-party managed; Qdrant Cloud extra cost |
| (d) Weaviate | Good filters, GraphQL/GRPC | Range supported | Expensive tiers | Heaviest ops, schema-heavy | Self-host EC2 only; no managed in AWS |
| (e) OpenSearch | Good via bool/term/range queries | Native range | High (managed OS) | Two clusters (vector + metadata) or one big one | Best-in-class managed AWS, but overkill/costly for MVP |

## Recommendation: (a) Postgres + pgvector on AWS RDS

- **One datastore** for relational (users, sessions, conversations, courses, citations)
  AND vectors — no dual-write, no sync jobs, transactions cover both.
- **Metadata filtering is first-class SQL**: `WHERE course_id = $1 AND start_ts <= $2
  AND resource_id = ANY($3) ORDER BY embedding <=> $q LIMIT 20` — timestamp range,
  course scoping, and citation linkage are trivial with B-tree/GIN indexes alongside
  HNSW/IVFFLAT vector index.
- **Cost**: smallest RDS (db.t4g.micro) fits hackathon; Chroma/Qdrant/Weaviate add a
  second service to run, secure, and back up.
- **Ops simplicity / AWS fit**: RDS gives backups, SSL, VPC isolation out of the box;
  FastAPI + asyncpg/SQLAlchemy/Prisma ergonomics.
- Migrate to OpenSearch/Qdrant later only if scale demands it — isolate vector access
  behind a repository interface for easy swap.

Schema sketch: `Resource(id, course_id, type)`, `Chunk(id, resource_id, course_id,
start_ts, end_ts, text, embedding vector(1536))`, `Citation(conv_id, chunk_id, span)`,
`@@index([course_id, start_ts])`, HNSW on embedding.

## Embedding Model Considerations
- Same model at ingest and query time; version it in the row (`embed_model` column)
  to allow re-embedding.
- OpenAI `text-embedding-3-small` (1536d): cheap, solid; or open BGE/E5-small if
  avoiding external API dependency during demo.
- Normalize to cosine distance; match DB dimension to model; index with HNSW after
  bulk load; chunk transcripts by ~30–60s windows or 300–500 tokens with overlap and
  keep timestamps on each chunk for citation anchoring.
