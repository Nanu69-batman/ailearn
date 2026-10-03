"""Phase 4 chat API tests (no network/DB).

Covers: HMAC accept/reject, post-T exclusion from answer context, unknown
citation refusal, empty-retrieval refusal, follow-up rewrite, quiz/summarize
timestamp scoping, plus review fixes C4/H1/H3/H4/H5/M3/M5/H6-partial/L4.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.chat import router as chat_router
from app.chat.launch import MAX_CURRENT_TIME_MS, sign_launch
from app.chat.llm import DeterministicStubLLM, LLMResult
from app.chat.prompts import REFUSAL_TEXT, rewrite_query
from app.chat.router import reset_chat_state, set_service_override
from app.chat.schemas import MAX_CURRENT_TIME_MS as SCHEMA_MAX_T
from app.chat.service import ChatService, apply_strict_timestamp
from app.config import settings
from app.ingestion.embedder import HashingEmbedder
from app.ingestion.models import Chunk, text_hash
from app.main import app
from app.retrieval.store import InMemoryVectorStore

T = 60_000
EMBEDDER = HashingEmbedder()
SECRET = "test-secret-key"


def make_chunk(
    cid: str,
    resource_id: str,
    source_type: str,
    text: str,
    start_ms: int | None = None,
    end_ms: int | None = None,
    page_no: int | None = None,
    course_id: str = "course1",
) -> Chunk:
    return Chunk(
        id=cid,
        resource_id=resource_id,
        course_id=course_id,
        source_type=source_type,
        chunk_index=0,
        text=text,
        text_hash=text_hash(text),
        embed_model=EMBEDDER.name,
        video_id="vid1" if source_type in ("video", "video_transcript") else None,
        start_ms=start_ms,
        end_ms=end_ms,
        page_no=page_no,
        permitted=True,
        embedding=EMBEDDER.embed(text),
    )


def fixture_store() -> InMemoryVectorStore:
    early = make_chunk(
        "c_early",
        "vid",
        "video_transcript",
        "photosynthesis converts light energy into chemical energy",
        5_000,
        12_000,
    )
    late = make_chunk(
        "c_late",
        "vid",
        "video_transcript",
        "what is photosynthesis light chemical energy spoiler finale",
        600_000,
        610_000,
    )
    pdf = make_chunk(
        "c_pdf",
        "handout",
        "pdf",
        "photosynthesis light chemical energy chapter notes",
        page_no=2,
    )
    return InMemoryVectorStore([early, late, pdf])


@pytest.fixture
def client(monkeypatch):
    reset_chat_state()
    monkeypatch.setattr(settings, "lms_launch_secret", SECRET)
    monkeypatch.setattr(settings, "llm_api_key", "")
    monkeypatch.setattr(settings, "allow_unsigned_launch", False)
    monkeypatch.setattr(settings, "use_in_memory", True)
    store = fixture_store()
    service = ChatService(store=store, embedder=EMBEDDER, llm=DeterministicStubLLM())
    set_service_override(service)
    c = TestClient(app)
    yield c
    reset_chat_state()


def launch_payload(**overrides):
    now_ms = int(time.time() * 1000)
    payload = {
        "learner_id": "learner1",
        "course_id": "course1",
        "resource_id": "vid",
        "lesson_id": "lesson1",
        "resource_scope": ["vid", "handout"],
        "current_time_ms": T,
        "issued_at": now_ms,
    }
    payload.update(overrides)
    return payload


def signed(payload: dict, secret: str = SECRET) -> dict:
    sig = sign_launch(
        secret,
        payload["learner_id"],
        payload["course_id"],
        payload["resource_id"],
        payload.get("lesson_id"),
        payload["resource_scope"],
        payload["current_time_ms"],
        payload["issued_at"],
    )
    return {**payload, "signature": sig}


def create_session(client: TestClient, **overrides) -> tuple[str, str]:
    payload = signed(launch_payload(**overrides))
    r = client.post("/lms/session", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("session_token")
    return body["session_id"], body["session_token"]


def post_chat(client: TestClient, sid: str, token: str, **overrides):
    payload = {
        "session_id": sid,
        "session_token": token,
        "current_time_ms": T,
        "message": "what is photosynthesis light chemical energy",
    }
    payload.update(overrides)
    return client.post("/chat", json=payload)


# --- HMAC launch -----------------------------------------------------------


def test_lms_session_accepts_valid_signature(client: TestClient):
    r = client.post("/lms/session", json=signed(launch_payload()))
    assert r.status_code == 200
    body = r.json()
    assert body["session_id"]
    assert body["learner_id"] == "learner1"
    assert body["session_token"]  # H3: launch token returned


def test_lms_session_rejects_bad_signature(client: TestClient):
    payload = signed(launch_payload())
    payload["signature"] = "0" * 64
    r = client.post("/lms/session", json=payload)
    assert r.status_code == 401
    assert r.json()["detail"] == "invalid launch"  # generic (M3)


def test_lms_session_rejects_stale_launch(client: TestClient):
    stale = int(time.time() * 1000) - (16 * 60 * 1000)
    r = client.post("/lms/session", json=signed(launch_payload(issued_at=stale)))
    assert r.status_code == 401
    assert r.json()["detail"] == "invalid launch"  # specifics server-side only


def test_lms_session_rejects_missing_signature_in_prod(client: TestClient):
    r = client.post("/lms/session", json=launch_payload())
    assert r.status_code == 401
    assert r.json()["detail"] == "invalid launch"


def test_lms_session_unsigned_dev_mode_requires_flag(client: TestClient, monkeypatch):
    # C4: dev-secret alone must NOT accept unsigned launches by default.
    monkeypatch.setattr(settings, "lms_launch_secret", "dev-secret")
    monkeypatch.setattr(settings, "allow_unsigned_launch", False)
    reset_chat_state()
    c = TestClient(app)
    r = c.post("/lms/session", json=launch_payload())
    assert r.status_code == 401


def test_lms_session_unsigned_dev_mode(client: TestClient, monkeypatch):
    # C4: explicit opt-in keeps the dev bypass; refusal otherwise is logged error-level.
    monkeypatch.setattr(settings, "lms_launch_secret", "dev-secret")
    monkeypatch.setattr(settings, "allow_unsigned_launch", True)
    reset_chat_state()
    c = TestClient(app)
    r = c.post("/lms/session", json=launch_payload())
    assert r.status_code == 200
    assert r.json()["session_token"]


# --- Session token binding (H3) --------------------------------------------


def test_chat_rejects_missing_session_token(client: TestClient):
    sid, _token = create_session(client)
    r = client.post(
        "/chat",
        json={"session_id": sid, "current_time_ms": T, "message": "hello world query"},
    )
    assert r.status_code == 401
    assert r.json()["detail"] == "invalid launch"


def test_chat_rejects_wrong_session_token(client: TestClient):
    sid, _token = create_session(client)
    r = post_chat(client, sid, "0" * 64)
    assert r.status_code == 401
    assert r.json()["detail"] == "invalid launch"


def test_history_rejects_missing_session_token(client: TestClient):
    sid, _token = create_session(client)
    r = client.get(f"/sessions/{sid}/messages")
    assert r.status_code == 401


def test_history_accepts_token_header(client: TestClient):
    sid, token = create_session(client)
    post_chat(client, sid, token)
    r = client.get(f"/sessions/{sid}/messages", headers={"x-session-token": token})
    assert r.status_code == 200
    assert r.json()["messages"]


def test_current_time_ms_ceiling_is_6h():
    assert MAX_CURRENT_TIME_MS == 6 * 3600 * 1000
    assert SCHEMA_MAX_T == 6 * 3600 * 1000


def test_chat_rejects_time_beyond_6h(client: TestClient):
    sid, token = create_session(client)
    r = post_chat(client, sid, token, current_time_ms=7 * 3600 * 1000)
    assert r.status_code == 422


def test_rate_limiter_blocks_after_window(client: TestClient, monkeypatch):
    monkeypatch.setattr(chat_router, "RATE_LIMIT_MAX", 3)
    chat_router.reset_rate_limits()
    sid, token = create_session(client)
    chat_router.reset_rate_limits()
    for _ in range(3):
        r = post_chat(client, sid, token)
        assert r.status_code == 200
    r = post_chat(client, sid, token)
    assert r.status_code == 429


# --- Chat grounding --------------------------------------------------------


def test_post_t_chunk_never_in_answer_context(client: TestClient):
    sid, token = create_session(client)
    r = post_chat(client, sid, token, video_id="vid1")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["refused"] is False
    assert "spoiler finale" not in body["answer"]
    assert "photosynthesis converts light energy" in body["answer"]
    ids = [c["chunk_id"] for c in body["citations"]]
    assert "c_late" not in ids
    assert "c_early" in ids


def test_unknown_citation_rejected(client: TestClient):
    class EvilLLM:
        async def complete(self, *, system, user_prompt, passages, mode):
            return LLMResult(text="Lies [chunk_id=evil_xyz]", cited_ids=["evil_xyz"])

    store = fixture_store()
    set_service_override(ChatService(store=store, embedder=EMBEDDER, llm=EvilLLM()))
    sid, token = create_session(client)
    r = post_chat(client, sid, token)
    assert r.status_code == 200
    body = r.json()
    assert body["refused"] is True
    assert body["citations"] == []
    assert body["answer"] == REFUSAL_TEXT


def test_uncited_non_refusal_answer_refused(client: TestClient):
    """H5: substantive answer with empty cited_ids must refuse."""

    class SilentLLM:
        async def complete(self, *, system, user_prompt, passages, mode):
            return LLMResult(text="Photosynthesis is great.", cited_ids=[])

    store = fixture_store()
    set_service_override(ChatService(store=store, embedder=EMBEDDER, llm=SilentLLM()))
    sid, token = create_session(client)
    r = post_chat(client, sid, token)
    assert r.status_code == 200
    body = r.json()
    assert body["refused"] is True
    assert body["citations"] == []
    assert body["answer"] == REFUSAL_TEXT


def test_refusal_text_passthrough_refused(client: TestClient):
    class RefusingLLM:
        async def complete(self, *, system, user_prompt, passages, mode):
            return LLMResult(text=REFUSAL_TEXT, cited_ids=[])

    store = fixture_store()
    set_service_override(ChatService(store=store, embedder=EMBEDDER, llm=RefusingLLM()))
    sid, token = create_session(client)
    r = post_chat(client, sid, token)
    assert r.status_code == 200
    assert r.json()["refused"] is True


def test_refusal_on_empty_retrieval(client: TestClient):
    empty = InMemoryVectorStore([])
    set_service_override(
        ChatService(store=empty, embedder=EMBEDDER, llm=DeterministicStubLLM())
    )
    sid, token = create_session(client)
    r = post_chat(client, sid, token, message="anything at all")
    assert r.status_code == 200
    body = r.json()
    assert body["refused"] is True
    assert body["citations"] == []
    assert "enough information" in body["answer"]


def test_chat_unknown_session_404(client: TestClient):
    r = client.post(
        "/chat",
        json={"session_id": "ses_missing", "current_time_ms": T, "message": "hi"},
    )
    assert r.status_code == 404


def test_provider_error_maps_to_502_redacted(client: TestClient):
    """M5: provider exceptions become 502 with a redacted detail."""

    class BoomLLM:
        async def complete(self, *, system, user_prompt, passages, mode):
            raise RuntimeError("super-secret-api-key=xyz upstream blew up")

    store = fixture_store()
    set_service_override(ChatService(store=store, embedder=EMBEDDER, llm=BoomLLM()))
    sid, token = create_session(client)
    r = post_chat(client, sid, token)
    assert r.status_code == 502
    assert r.json()["detail"] == "upstream provider error"
    assert "super-secret" not in r.text


def test_unhandled_error_returns_json_internal_error(client: TestClient):
    """M5: unexpected errors -> JSON {"detail": "internal error"} + x-request-id."""

    class WeirdLLM:
        async def complete(self, *, system, user_prompt, passages, mode):
            raise ValueError("boom-internal-detail")

    store = fixture_store()
    set_service_override(ChatService(store=store, embedder=EMBEDDER, llm=WeirdLLM()))
    sid, token = create_session(client)
    r = post_chat(client, sid, token)
    assert r.status_code == 500
    assert r.json() == {"detail": "internal error"}
    assert r.headers.get("x-request-id")
    assert "boom-internal" not in r.text


# --- H1 strict-timestamp exact end_ms semantics ----------------------------


def test_apply_strict_timestamp_end_ms_semantics():
    vid_null_end = make_chunk("v_null", "vid", "video_transcript", "t", 1_000, None)
    vid_late = make_chunk("v_late", "vid", "video_transcript", "t", 1_000, T + 1)
    vid_edge = make_chunk("v_edge", "vid", "video_transcript", "t", T, T)
    vid_early = make_chunk("v_early", "vid", "video", "t", 90_000, 10_000)
    doc_late_start = make_chunk(
        "d1", "handout", "pdf", "t", 999_999_999, None, page_no=1
    )
    doc_null = make_chunk("d2", "handout", "pdf", "t", None, None, page_no=2)
    passages = [vid_null_end, vid_late, vid_edge, vid_early, doc_late_start, doc_null]
    kept = apply_strict_timestamp(passages, T, "quiz")
    kept_ids = {p.id for p in kept}
    assert kept_ids == {"v_edge", "v_early", "d1", "d2"}
    # non-strict modes pass everything through untouched
    assert len(apply_strict_timestamp(passages, T, "qa")) == len(passages)


# --- H4 settings-driven factories ------------------------------------------


def test_default_service_uses_stub_without_key(monkeypatch):
    from app.chat.llm import DeterministicStubLLM
    from app.chat.router import describe_active_providers, get_service

    monkeypatch.setattr(settings, "llm_api_key", "")
    monkeypatch.setattr(settings, "use_in_memory", True)
    reset_chat_state()
    svc = get_service()
    assert isinstance(svc._llm, DeterministicStubLLM)
    assert isinstance(svc._store, InMemoryVectorStore)
    providers = describe_active_providers()
    assert providers["llm"] == "stub"
    assert providers["store"] == "in-memory"
    reset_chat_state()


def test_health_db_reports_status_without_db(client: TestClient):
    r = client.get("/health/db")
    assert r.status_code == 200
    assert r.json()["status"] in ("up", "down")


# --- Follow-up rewrite -----------------------------------------------------


def test_rewrite_query_uses_history():
    out = rewrite_query("why does it happen?", ["what is photosynthesis?"])
    assert "photosynthesis" in out.lower()
    # self-contained question passes through unchanged
    standalone = rewrite_query(
        "Explain the Krebs cycle stages in cellular respiration detail",
        ["what is photosynthesis?"],
    )
    assert standalone.startswith("Explain the Krebs")


def test_followup_chat_uses_history_context(client: TestClient):
    sid, token = create_session(client)
    r1 = post_chat(client, sid, token)
    assert r1.status_code == 200
    assert r1.json()["refused"] is False
    r2 = post_chat(client, sid, token, message="why does it happen?")
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["refused"] is False
    ids = [c["chunk_id"] for c in body["citations"]]
    assert ids  # history-resolved query still retrieves evidence
    assert "c_late" not in ids


# --- Modes -----------------------------------------------------------------


@pytest.mark.parametrize("mode", ["explain_simple", "analogy", "summarize", "compare"])
def test_learning_modes_answer_from_context(client: TestClient, mode: str):
    sid, token = create_session(client)
    r = post_chat(client, sid, token, mode=mode)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["refused"] is False
    assert "spoiler finale" not in body["answer"]
    assert body["citations"]


def test_quiz_scoped_to_t(client: TestClient):
    sid, token = create_session(client)
    r = post_chat(client, sid, token, message="quiz me on photosynthesis", mode="quiz")
    assert r.status_code == 200
    body = r.json()
    assert body["refused"] is False
    assert "spoiler finale" not in body["answer"]
    assert all(c["chunk_id"] != "c_late" for c in body["citations"])


def test_summarize_scoped_to_t(client: TestClient):
    sid, token = create_session(client)
    r = post_chat(
        client, sid, token, message="summarize what I learned", mode="summarize"
    )
    assert r.status_code == 200
    body = r.json()
    assert "spoiler finale" not in body["answer"]
    assert all(c["chunk_id"] != "c_late" for c in body["citations"])


def test_messages_history_roundtrip(client: TestClient):
    # L4: MessageOut.citations is list[Citation] (typed, not dict-union).
    from app.chat.schemas import MessageOut

    sid, token = create_session(client)
    post_chat(client, sid, token)
    r = client.get(f"/sessions/{sid}/messages?session_token={token}")
    assert r.status_code == 200
    msgs = r.json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[1]["citations"]
    parsed = [MessageOut(**m) for m in msgs]
    assert parsed[1].citations and parsed[1].citations[0].chunk_id
    # request-id middleware echoes a request id
    assert r.headers.get("x-request-id")


def test_request_validation(client: TestClient):
    sid, token = create_session(client)
    r = post_chat(client, sid, token, message="")
    assert r.status_code == 422


def test_get_service_wires_embedder_via_factory(monkeypatch):
    """Regression: get_service must pass an explicit embedder so the query
    embedding matches the indexed chunk dimension (hasher-vs-Gemini mismatch
    silently produced garbage ranking and correct-but-useless refusals)."""
    import app.chat.router as router_mod
    import app.ingestion.embedder as embedder_mod

    calls: list[str] = []
    real_factory = embedder_mod.get_embedder

    def counting_factory():
        calls.append("embedder")
        return real_factory()

    monkeypatch.setattr(embedder_mod, "get_embedder", counting_factory)
    monkeypatch.setattr(router_mod, "_service_override", None)
    try:
        service = router_mod.get_service()
        assert calls == ["embedder"]
        assert service._embedder is not None
    finally:
        router_mod.set_service_override(None)
