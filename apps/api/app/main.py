import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.chat.attachments import router as chat_attachments_router
from app.chat.chats import router as malai_chats_router
from app.chat.router import describe_active_providers
from app.chat.router import router as chat_router
from app.config import settings
from app.ingestion.backoff import QuotaExhaustedError
from app.ingestion.router import router as ingestion_router
from app.chat.models import ModelChoiceMiddleware
from app.middleware import RequestIDMiddleware
from app.subjects.router import router as subjects_router
from app.voice.router import router as voice_router

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
    if settings.supabase_enabled():
        # Supabase is the durable store: load from it instead of the JSON
        # files, and leave JSON mirroring off so the two cannot diverge.
        from app.chat.sessions import load_sessions_from_supabase
        from app.subjects.store import (
            load_subjects_from_supabase,
            seed_demo_subjects_to_supabase,
        )

        load_subjects_from_supabase()
        seed_demo_subjects_to_supabase()
        load_sessions_from_supabase()
    if settings.use_in_memory:
        # Sessions/messages are process-local; mirror them to data/ so an API
        # restart does not 404 every open browser tab (see chat/sessions.py).
        from app.chat.sessions import load_session_state, set_persistence_enabled

        if not settings.supabase_enabled():
            set_persistence_enabled(True)
            load_session_state()

            from app.chat import chats as malai_chats

            malai_chats.set_persistence_enabled(True)
            malai_chats.load_local_state()

            from app.chat import attachments as chat_attachments

            chat_attachments.set_persistence_enabled(True)
            chat_attachments.load_local_state()

        # No snapshot restore: the Chroma PersistentClient under data/chroma is
        # the durable store, so there is no `store_snapshot.pkl` to load and
        # ingest state needs no rehydration -- indexed chunks outlive restarts.
        # Opened here (not at import) so importing `app.main` stays side-effect
        # free for tests.
        from app.retrieval.registry import get_shared_store

        store = get_shared_store()
        log.info("retrieval store ready: %d chunk(s)", store.count())

        # Subjects are the user's unit of organisation and must survive a
        # restart, same reasoning as sessions above.
        from app.subjects.store import load_subject_state, set_persistence_enabled

        if not settings.supabase_enabled():
            set_persistence_enabled(True)
            load_subject_state()
    yield


app = FastAPI(title="AI Learning Assistant API", version="0.1.0", lifespan=lifespan)


@app.exception_handler(QuotaExhaustedError)
async def _quota_exhausted(_request: Request, exc: QuotaExhaustedError) -> JSONResponse:
    # A spent daily quota is a service state, not a server bug: say so plainly.
    return JSONResponse(status_code=503, content={"detail": str(exc)})


app.add_middleware(RequestIDMiddleware)
app.add_middleware(ModelChoiceMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.cors_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat_router)

# The Malai sidebar's chat list, per signed-in user: /malai/chats.
app.include_router(malai_chats_router)

# Images attached to one chat (context only, never resources): /chat/attachments.
app.include_router(chat_attachments_router)

# The library-based ingestion pipeline replaces the deleted hand-built router.
# Route shapes are unchanged because `apps/web/lib/api.ts` still calls them:
#   POST /courses/{id}/ingest          (file upload)
#   POST /courses/{id}/ingest-youtube  {url}
#   GET  /ingestion/jobs/{id}
#   GET  /courses/{id}/resources
app.include_router(ingestion_router)

# Subject organisation: POST/GET /subjects, plus per-subject resource
# attach/detach. Additive; no existing route shape is touched.
app.include_router(subjects_router)

# Live voice: GET /voice/models, WS /voice/live, WS /voice/transcribe.
app.include_router(voice_router)


@app.get("/dev/trace")
def dev_trace(after: int = 0, course_id: str | None = None) -> dict:
    """Pipeline step events after ``after``, for the developer view to animate."""
    from app import devtrace

    return {
        "enabled": devtrace.enabled(),
        "events": devtrace.events_after(max(after, 0), course_id),
        "latest": devtrace.latest_seq(),
        "where": {
            k: devtrace.where(k) for k in ("asr", "vision", "embed", "store", "llm", "agent")
        },
        "agent": bool(settings.openrouter_api_key),
    }


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
