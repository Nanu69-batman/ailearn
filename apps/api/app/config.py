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
        from app.ingestion.embedder import EMBED_DIM, GEMINI_EMBED_DIM

        return GEMINI_EMBED_DIM if self.embedding_api_key else EMBED_DIM


settings = Settings()
