import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.chat.router import describe_active_providers
from app.chat.router import router as chat_router
from app.config import settings
from app.ingestion.router import router as ingestion_router
from app.middleware import RequestIDMiddleware

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    providers = describe_active_providers()
    log.info(
        "providers active: llm=%s store=%s sessions=%s use_in_memory=%s",
        providers["llm"],
        providers["store"],
        providers["sessions"],
        settings.use_in_memory,
    )
    if settings.use_in_memory:
        # Sessions/messages are process-local; mirror them to data/ so an API
        # restart does not 404 every open browser tab (see chat/sessions.py).
        from app.chat.sessions import load_session_state, set_persistence_enabled

        set_persistence_enabled(True)
        load_session_state()

        from app.retrieval.registry import load_snapshot

        loaded = load_snapshot()
        if loaded:
            log.info("restored %d chunks from local snapshot", loaded)
        from app.ingestion.router import (
            ensure_resources_for_chunks,
            load_ingest_state,
        )
        from app.retrieval.registry import get_shared_store

        if load_ingest_state() == 0:
            ensure_resources_for_chunks(get_shared_store()._chunks)
    yield


app = FastAPI(title="AI Learning Assistant API", version="0.1.0", lifespan=lifespan)

app.add_middleware(RequestIDMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.cors_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat_router)
app.include_router(ingestion_router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/health/db")
async def health_db() -> dict:
    """Ping the database with SELECT 1 (short timeout); report down on failure."""
    dsn = (settings.database_url or "").replace(
        "postgresql+asyncpg://", "postgresql://"
    )
    if not dsn:
        return {"status": "down", "database_url_set": False}
    try:
        import asyncpg

        conn = await asyncio.wait_for(asyncpg.connect(dsn), timeout=2.0)
        try:
            await asyncio.wait_for(conn.fetchval("SELECT 1"), timeout=2.0)
        finally:
            await conn.close()
        return {"status": "up", "database_url_set": True}
    except Exception as exc:  # noqa: BLE001 - ping must report down on any failure
        log.warning("db health ping failed: %s", type(exc).__name__)
        return {"status": "down", "database_url_set": bool(settings.database_url)}
