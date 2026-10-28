from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve the env file next to this module (apps/api/.env) instead of relying on
# the process CWD. `env_file=".env"` silently reads a *different* .env when
# uvicorn is started from the repo root, which leaves llm_api_key empty and
# silently downgrades the app to DeterministicStubLLM + hashing embedder.
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(ENV_FILE), extra="ignore")

    database_url: str = "postgresql+asyncpg://localhost:5432/ailearn"
    llm_api_key: str = ""
    llm_model: str = "gemini-3.5-flash-lite"
    embedding_api_key: str = ""
    embedding_model: str = "gemini-embedding-2"
    # "pinecone" embeds chunks and queries with Pinecone Inference (needs
    # PINECONE_API_KEY and a 1024-d index). Anything else keeps the old
    # choice: Gemini when EMBEDDING_API_KEY is set, else offline hashing.
    embedding_provider: str = ""
    pinecone_embed_model: str = "llama-text-embed-v2"
    # Hosted transcription. Empty key = local faster-whisper only.
    groq_api_key: str = ""
    groq_transcribe_model: str = "whisper-large-v3-turbo"
    yt_data_dir: str = "data/assets/youtube"
    yt_max_minutes: int = 45
    cors_origin: str = "http://localhost:3000"
    lms_launch_secret: str = "dev-secret"
    cache_enabled: bool = True
    # C4 fail-closed auth: unsigned launches are refused unless this is
    # explicitly set to true (dev only). Never enable in production.
    allow_unsigned_launch: bool = False
    # H4 wiring: USE_IN_MEMORY=true (default) keeps process-local session,
    # message, and vector stores (tests/dev). Set false to select the
    # Postgres-backed vector store when DATABASE_URL looks real and reachable.
    use_in_memory: bool = True
    # Free-tier protection for a public deployment. 0 = no cap (local dev).
    # Every upload spends Gemini embedding/vision requests from a shared quota.
    max_upload_mb: int = 0
    max_video_minutes: int = 0
    max_uploads_per_hour: int = 0
    # Hosted backends. Defaults keep everything on local disk (dev + tests).
    vector_store: str = "chroma"  # "chroma" | "pinecone"
    pinecone_api_key: str = ""
    pinecone_index: str = "ailearn"
    storage: str = "local"  # "local" | "r2" (mirror data/ files to R2)
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket: str = ""
    # Local faster-whisper is the fallback when Groq fails. Its model needs
    # more RAM than a 512 MB free instance has; turn it off there.
    local_whisper_fallback: bool = True
    # Agentic chat: an OpenRouter model answers by calling time-gated tools
    # (search_lesson / get_lesson_content). Empty key = the classic pipeline.
    # Any agent failure (quota, timeout, bad tool call) falls back to it too.
    # Which models answer is the learner's mode (auto/fast/balanced/powerful)
    # or their own provider key and model, per request: app/chat/models.py.
    openrouter_api_key: str = ""
    # Model calls per answer. The lesson read is prefetched, so 2 = answer
    # (or one extra tool call) + the final reply.
    agent_max_steps: int = 2
    # No time limit by default: a slow free-tier reply is waited for, not cut
    # off. Set either to a number of seconds to bring a limit back.
    agent_timeout_s: float | None = None  # per provider call
    agent_total_timeout_s: float | None = None  # whole tool loop
    # Developer view: record pipeline steps for GET /dev/trace. DEV_TRACE=false
    # turns recording off (the endpoint then returns no events).
    dev_trace: bool = True
    # Supabase (docs/SUPABASE_PLAN.md). Features turn on only when both the
    # URL and the service-role key are set; the key is server only (it
    # bypasses RLS). JWT_SECRET is for legacy HS256 projects; empty = verify
    # access tokens through the project's JWKS (ES256/RS256).
    supabase_url: str = ""
    supabase_service_role_key: str = ""
    supabase_jwt_secret: str = ""
    supabase_storage_bucket: str = "media"
    # AUTH_REQUIRED=true refuses anonymous callers on gated routes (deployed).
    # Default false keeps tests and local dev working without a login.
    auth_required: bool = False

    # Live voice (docs/VOICE_PROTOCOL.md). Model ids are config so a renamed
    # model is a settings change. The key is llm_api_key.
    voice_live_model: str = "gemini-3.8-live"
    voice_thinking_model: str = "gemini-3.8-live-extended-thinking"
    voice_native_audio_model: str = "gemini-2.5-flash-native-audio-preview-12-2025"
    voice_transcribe_model: str = "gemini-3.5-transcribe-live"
    voice_thinking_level: str = "medium"  # low | medium | high

    def supabase_enabled(self) -> bool:
        """True only when both ``supabase_url`` and the service-role key are set."""
        return bool(self.supabase_url and self.supabase_service_role_key)

    def expected_embedding_dim(self) -> int:
        """Vector width every chunk and every query must agree on.

        Ingestion embeds with ``app.ingestion.embedder.get_default_embedder()``,
        which is Gemini (3072) when ``embedding_api_key`` is set and the offline
        hashing embedder (1536) otherwise. Retrieval must use the *same* choice
        or cosine scores are computed across mismatched widths. This property
        is the single place that encodes that mapping so callers (and the
        re-index tooling) cannot drift from it.

        Imported lazily inside the method: ``app.ingestion.embedder`` imports
        this module at import time, so a top-level import would be circular.
        """
        from app.ingestion.embedder import (
            EMBED_DIM,
            GEMINI_EMBED_DIM,
            PINECONE_EMBED_DIM,
        )

        if self.embedding_provider == "pinecone":
            return PINECONE_EMBED_DIM
        return GEMINI_EMBED_DIM if self.embedding_api_key else EMBED_DIM


settings = Settings()
