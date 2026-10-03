# Research: Ingestion Pipeline Design

## 1. Pipeline Stages

1. **Extract** – pull raw text + structural metadata from each source.
2. **Normalize** – unify into a canonical `Document`/`Segment` model (plain text, unicode normalized, boilerplate stripped).
3. **Chunk** – split into retrieval-sized windows, never crossing a source boundary segment (timestamp span / page / slide) so citations stay exact.
4. **Embed** – batch embed chunk text via an embedding model (local or API).
5. **Index** – upsert vectors + metadata into the vector store and record ingestion state.

## 2. Libraries per Stage

| Stage | Library |
|---|---|
| Video transcript | `faster-whisper` (preferred, CTranslate2, CPU int8 / GPU) for ASR; fallback: parse existing `.vtt`/`.srt` with `webvtt-py` / stdlib `srt`; keep segment start/end seconds |
| PDF | `pymupdf` (fitz) — fast, page-level text + page numbers; fallback `pdfplumber` for tables |
| PPT/PPTX | `python-pptx` — iterate slides, extract shape text, slide numbers |
| Notes / text / code | stdlib `pathlib` + `chardet` for encoding; code files read as text with language tag from extension |
| Chunking | `langchain-text-splitters` (`RecursiveCharacterTextSplitter`) or simple token-window splitter via `tiktoken` |
| Embeddings | `sentence-transformers` (local, e.g. `all-MiniLM-L6-v2`) or OpenAI `text-embedding-3-small` |
| Index | `pgvector` (Postgres) or `chromadb`/`faiss` for hackathon speed |

## 3. Chunk Metadata Schema

```json
{
  "chunk_id": "uuid",
  "course_id": "string",
  "source_id": "string (asset id, not LLM-controlled)",
  "source_type": "video | pdf | pptx | note | code",
  "resource_scope": ["public", "enrolled", "instructor"],
  "video_id": "string | null",
  "start_s": 12.34, "end_s": 18.90,
  "page_no": 4,
  "slide_no": 7,
  "chunk_index": 3,
  "text_hash": "sha1 of normalized text",
  "asset_uri": "s3://bucket/key | local path",
  "ingested_at": "iso8601",
  "ingest_version": 1
}
```

Application-owned metadata (ids, scope, offsets) is stored alongside the vector, never inside the embedded text.

## 4. Raw Asset Storage

- **Hackathon default:** local filesystem under `data/assets/<course_id>/<source_id>/`, referenced by `asset_uri`.
- **Production path:** S3-compatible object storage (`boto3` / MinIO); same URI convention (`s3://bucket/course/source/file`).
- DB stores only the URI + checksum (`sha256`), not the bytes. Binary assets stay out of the vector index.

## 5. Idempotency / Re-ingestion

- Deterministic `chunk_id = sha1(source_id + segment key + text_hash)`; re-running upserts instead of duplicating.
- Content-hash check per asset: if `sha256(file)` unchanged and `ingest_version` current, skip (fast path).
- On change: delete existing chunks where `source_id` matches (delete-by-source), then re-chunk/embed/index.
- Track per-asset status (`pending | processing | done | failed`) in an `ingestion_jobs` table so failures are retryable and chunks are never half-committed — wrap index upsert in one transaction per source.
