"""Shared pytest fixtures for the API suite.

Purpose: every test starts from the same process-global state that the app
starts from. Without this, ``app.chat.sessions``'s ``session_store`` /
``message_store`` singletons and the aliases ``app.chat.router`` holds for them
leak across tests, and the suite becomes order-dependent.

Concretely: ``app.chat.router.create_lms_session`` writes to the store object
bound in ``app.chat.router``, while ``ChatService.__init__`` resolves
``app.chat.sessions.session_store`` at construction time. If any test rebinds
only one of those names, a session is created in store A and looked up in
store B, and ``/chat`` answers 404 "session not found" for a session that was
just created -- reproducible only when the leaking test runs first (this was
``test_rewrite_never_widens_scope``, which passed alone but failed in a full
``pytest tests/test_chat.py`` run).

This fixture makes that class of leak harmless rather than relying on every
test author remembering to restore the aliases: the canonical singleton
objects are captured once, re-bound in both modules before the test, and
cleared after it.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_hosted_transcription(monkeypatch):
    """Never call Groq from tests, even when the developer's .env has a key."""
    from app.config import settings
    from app.ingestion.backoff import clear_quota_block

    monkeypatch.setattr(settings, "groq_api_key", "")
    # Nor the Gemini intent router (app/chat/intent_model.py).
    monkeypatch.setattr(settings, "intent_router_enabled", False)
    clear_quota_block()
    yield
    clear_quota_block()


@pytest.fixture(autouse=True)
def _no_live_supabase(monkeypatch):
    """Never write to the developer's real Supabase project from tests.

    Without this, a .env with Supabase keys made every test that creates a
    subject, resource or session mirror it into the live database, where the
    app then loaded it back. Tests that need Supabase inject a
    ``MockTransport`` client with ``set_client()`` and set these themselves.
    """
    from app.config import settings
    from app.supa import client as supa_client

    monkeypatch.setattr(settings, "supabase_url", "")
    monkeypatch.setattr(settings, "supabase_service_role_key", "")
    supa_client.set_client(None)
    yield
    supa_client.set_client(None)


@pytest.fixture(autouse=True)
def _isolate_process_global_state():
    """Give every test a clean, consistent view of the module-level singletons."""
    from app.chat import router as chat_router
    from app.chat import sessions as sessions_mod

    # TODO(library-rebuild): the `app.retrieval.registry` import and the
    # `registry_mod.SNAPSHOT_PATH = None` reset below were removed with the
    # hand-built store. Restore them (or their Chroma equivalent) when the new
    # store lands, so snapshot persistence can never be enabled against a real
    # developer data/ directory.

    # Captured at import time, not per-test: the canonical objects the app
    # uses. Per-test capture would bake in whatever a previous test leaked.
    canonical_sessions = sessions_mod.session_store
    canonical_messages = sessions_mod.message_store

    def _rebind() -> None:
        # Force the module and the router to agree, whatever a previous test
        # (or an in-flight monkeypatch) left behind.
        sessions_mod.session_store = canonical_sessions
        sessions_mod.message_store = canonical_messages
        chat_router.session_store = canonical_sessions
        chat_router.message_store = canonical_messages

    _rebind()
    canonical_sessions.clear()
    canonical_messages.clear()

    yield

    _rebind()
    canonical_sessions.clear()
    canonical_messages.clear()
    # Snapshot persistence must stay off unless a test explicitly turns it on
    # against a tmp_path; never let a run write to the developer's data/.
    sessions_mod.set_persistence_enabled(False)


# ---------------------------------------------------------------------------
# Ingestion / retrieval fixtures (ADR-002 rebuild)
# ---------------------------------------------------------------------------

from tests.store_helpers import make_chunk, make_embedder


@pytest.fixture
def embedder():
    """Offline hashing embedder -- never downloads an ONNX model."""
    return make_embedder()


@pytest.fixture
def chroma_store(tmp_path, embedder):
    """A real Chroma store rooted in ``tmp_path``.

    Real rather than a double: the scope and strict-T obligations live in the
    store's query, so a list-backed fake would test the fake (see the honesty
    note at the top of ``test_chat_safety.py``). Rooted in tmp_path so no test
    can write to the developer's ``data/``.
    """
    from app.retrieval.store import ChromaVectorStore

    return ChromaVectorStore(
        path=tmp_path / "chroma", embedder=embedder, collection="test_chunks"
    )


@pytest.fixture
def make_chunk_fixture():
    return make_chunk