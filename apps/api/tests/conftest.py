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
def _isolate_process_global_state():
    """Give every test a clean, consistent view of the module-level singletons."""
    from app.chat import router as chat_router
    from app.chat import sessions as sessions_mod
    from app.retrieval import registry as registry_mod

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
    registry_mod.SNAPSHOT_PATH = None