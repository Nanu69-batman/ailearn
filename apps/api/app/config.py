from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

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


settings = Settings()
