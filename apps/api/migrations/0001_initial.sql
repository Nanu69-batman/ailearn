-- 0001_initial.sql: core schema for AI Learning Assistant prototype
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE courses (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE resources (
  id TEXT PRIMARY KEY,
  course_id TEXT NOT NULL REFERENCES courses(id),
  type TEXT NOT NULL CHECK (type IN ('video','pdf','pptx','note','code')),
  asset_uri TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE chunks (
  id TEXT PRIMARY KEY,
  resource_id TEXT NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
  course_id TEXT NOT NULL REFERENCES courses(id),
  source_type TEXT NOT NULL,
  video_id TEXT,
  start_ms BIGINT,
  end_ms BIGINT,
  page_no INT,
  slide_no INT,
  chunk_index INT NOT NULL,
  text TEXT NOT NULL,
  text_hash TEXT NOT NULL,
  embed_model TEXT NOT NULL,
  -- Native dim of gemini-embedding-2 is 3072 (nothing deployed yet; safe to widen).
  embedding vector(3072),
  permitted BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_chunks_course_start ON chunks(course_id, start_ms);
CREATE INDEX idx_chunks_resource ON chunks(resource_id);
CREATE INDEX idx_chunks_embedding ON chunks USING hnsw (embedding vector_cosine_ops);

CREATE TABLE sessions (
  id TEXT PRIMARY KEY,
  learner_id TEXT NOT NULL,
  course_id TEXT NOT NULL REFERENCES courses(id),
  video_id TEXT,
  resource_scope JSONB NOT NULL DEFAULT '[]',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE messages (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  role TEXT NOT NULL CHECK (role IN ('user','assistant')),
  content TEXT NOT NULL,
  citations JSONB NOT NULL DEFAULT '[]',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE ingestion_jobs (
  id TEXT PRIMARY KEY,
  resource_id TEXT NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','processing','done','failed')),
  ingest_version INT NOT NULL DEFAULT 1,
  error TEXT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
