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


def test_unsigned_launch_allowed_in_dev_mode(client: TestClient, monkeypatch):
    """Regression: local demo sends unsigned launches; dev secret + flag must pass."""
    monkeypatch.setattr(settings, "lms_launch_secret", "dev-secret")
    monkeypatch.setattr(settings, "allow_unsigned_launch", True)
    reset_chat_state()
    c = TestClient(app)
    r = c.post("/lms/session", json=launch_payload())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["session_token"]
    assert body["learner_id"] == "learner1"


def test_unsigned_launch_rejected_with_prod_secret(client: TestClient, monkeypatch):
    """Production guard: a non-dev secret must refuse unsigned launches even
    when ALLOW_UNSIGNED_LAUNCH is true (misconfiguration must fail closed)."""
    monkeypatch.setattr(settings, "lms_launch_secret", "prod-secret-key")
    monkeypatch.setattr(settings, "allow_unsigned_launch", True)
    reset_chat_state()
    c = TestClient(app)
    r = c.post("/lms/session", json=launch_payload())
    assert r.status_code == 401
    assert r.json()["detail"] == "invalid launch"


def test_wrong_signature_rejected_in_dev_mode(client: TestClient, monkeypatch):
    """Dev bypass only covers an ABSENT signature; a bogus one must still fail."""
    monkeypatch.setattr(settings, "lms_launch_secret", "dev-secret")
    monkeypatch.setattr(settings, "allow_unsigned_launch", True)
    reset_chat_state()
    c = TestClient(app)
    r = c.post("/lms/session", json=launch_payload(signature="dev"))
    assert r.status_code == 401
    assert r.json()["detail"] == "invalid launch"


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


def test_citation_markers_stripped_from_answer(client: TestClient):
    """Markers are transport format: hidden from users, citations intact."""

    class MarkerLLM:
        async def complete(self, *, system, user_prompt, passages, mode):
            cid = passages[0].chunk_id
            return LLMResult(
                text=f"In simple terms: light becomes sugar [chunk_id={cid}].",
                cited_ids=[cid],
            )

    store = fixture_store()
    set_service_override(ChatService(store=store, embedder=EMBEDDER, llm=MarkerLLM()))
    sid, token = create_session(client)
    r = post_chat(client, sid, token)
    assert r.status_code == 200
    body = r.json()
    assert body["refused"] is False
    assert "[chunk_id=" not in body["answer"]
    assert "chunk_id" not in body["answer"]
    # Markers gone, prose kept, punctuation not orphaned.
    assert "In simple terms:" in body["answer"]
    assert " ." not in body["answer"]
    ids = [c["chunk_id"] for c in body["citations"]]
    assert ids, "citations must still be populated after stripping"
    assert any(i in ids for i in ("c_early", "c_late", "c_doc")) or ids


def test_citation_validation_unaffected_by_stripping(client: TestClient):
    """An unknown-ID marker must still trigger refusal, not be stripped away."""

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
    assert "evil_xyz" not in body["answer"]


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


def test_unknown_scope_refuses(client: TestClient):
    """Scope IDs matching nothing indexed must refuse (placeholder-scope bug)."""
    sid, token = create_session(
        client, resource_scope=["res_video", "res_pdf", "res_ppt"]
    )
    r = post_chat(client, sid, token)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["refused"] is True
    assert body["citations"] == []


def test_known_scope_answers(client: TestClient):
    """Scope matching a real indexed resource must answer with citations."""
    sid, token = create_session(client, resource_scope=["vid"])
    r = post_chat(client, sid, token)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["refused"] is False
    assert body["citations"]


# ---------------------------------------------------------------------------
# Bunched citation markers (production regression).
#
# Gemini bunches IDs under one bracket and repeats the key for each one:
# ``[chunk_id=aaa, chunk_id=bbb]``. The old extraction/strip pattern had a
# character class without ``=``, so that whole bracket never matched. Two
# failures followed from the one defect: the marker leaked into the visible
# answer, and (worse) the IDs inside it were never extracted, so they were
# never validated and never became citations -- a video-focused answer built
# from video passages came back citing the PDF.
# ---------------------------------------------------------------------------


def test_strip_handles_bunched_citation_markers():
    """Every marker shape Gemini emits is removed; prose and punctuation stay."""

    from app.chat.prompts import strip_citation_markers

    cases = {
        # single marker
        "single": ("A derivative [chunk_id=abc].", "A derivative."),
        # bunched with the key repeated per id
        "bunched": (
            "derivatives are similar [chunk_id=aaa, chunk_id=bbb]. Next.",
            "derivatives are similar. Next.",
        ),
        # bunched with bare ids after the first
        "bare": (
            "derivatives [chunk_id=aaa, bbb, ccc] and more",
            "derivatives and more",
        ),
        # end of sentence, with and without trailing punctuation
        "eol": (
            "Rate of change [chunk_id=aaa, chunk_id=bbb].",
            "Rate of change.",
        ),
        # adjacent to punctuation, and on its own line (blank line collapses
        # to a paragraph break; no marker and no stray bracket survives)
        "adjacent": (
            "see [chunk_id=aaa],\n[chunk_id=bbb]\ndone",
            "see,\n\ndone",
        ),
        # separated by newlines / mid-sentence
        "spaced": (
            "one [chunk_id=aaa]\ntwo [chunk_id=bbb] three",
            "one\ntwo three",
        ),
    }
    for name, (raw, expected) in cases.items():
        cleaned = strip_citation_markers(raw)
        assert "chunk_id" not in cleaned, f"{name}: marker leaked -> {cleaned!r}"
        assert "[" not in cleaned or "[" in expected, f"{name}: stray bracket"
        assert cleaned == expected, f"{name}: got {cleaned!r} want {expected!r}"

    # Text with no markers at all is returned unchanged (no reflow damage).
    plain = "A plain answer with (parentheses) and a hyphen-word, plus 3.14."
    assert strip_citation_markers(plain) == plain
    assert strip_citation_markers("") == ""


def test_strip_does_not_break_citation_validation(client: TestClient):
    """An unknown ID is still refused -- stripping must not rescue it.

    Also asserts that a bunched marker containing an unknown ID is refused,
    which is the safety property the old pattern silently violated: it could
    not read those IDs at all, so they were never validated.
    """
    from app.chat.prompts import extract_cited_ids

    # Extraction reads every shape, so validation sees every ID.
    assert extract_cited_ids("x [chunk_id=a, chunk_id=b] y") == ["a", "b"]
    assert extract_cited_ids("x [chunk_id=a, b, c] y") == ["a", "b", "c"]
    assert extract_cited_ids("x [chunk_id=a] y") == ["a"]

    class EvilBunchedLLM:
        async def complete(self, *, system, user_prompt, passages, mode):
            cid = passages[0].chunk_id
            return LLMResult(
                text=f"Mostly true [chunk_id={cid}, chunk_id=evil_xyz].",
                cited_ids=[cid],
            )

    store = fixture_store()
    set_service_override(
        ChatService(store=store, embedder=EMBEDDER, llm=EvilBunchedLLM())
    )
    # The ``client`` fixture provides the signed-launch secret; only the LLM is
    # swapped out here.
    sid, token = create_session(client)
    r = post_chat(client, sid, token)
    assert r.status_code == 200, r.text
    body = r.json()
    # The unknown ID is read, rejected, and the whole answer is refused.
    assert body["refused"] is True
    assert body["citations"] == []
    assert body["answer"] == REFUSAL_TEXT
    assert "evil_xyz" not in body["answer"]


def test_focus_returns_video_citations_end_to_end(client: TestClient):
    """Focused video retrieval must surface video citations through /chat.

    Regression: with the focused video resource, the answer was built from
    video passages but the response cited the PDF. The video IDs were emitted
    inside a bunched marker that the extraction pattern could not read, so only
    the model's trailing single-ID PDF marker survived. This test reproduces
    that exact shape -- a bunched first marker, a trailing PDF marker -- and
    asserts the video citations come back.
    """

    class BunchedLLM:
        """Mimics Gemini: bunches the top two passages under one bracket."""

        async def complete(self, *, system, user_prompt, passages, mode):
            video_ids = [p.chunk_id for p in passages if p.source_type == "video"][:2]
            pdf_ids = [p.chunk_id for p in passages if p.source_type != "video"][:1]
            bunched = ", ".join(f"chunk_id={c}" for c in video_ids)
            text = (
                f"A partial derivative is similar to an ordinary one "
                f"[{bunched}]. Notes on page one [chunk_id={pdf_ids[0]}]."
            )
            return LLMResult(text=text, cited_ids=[])

    pdf_resource = "res_pdf_focus_e2e"
    video_resource = "res_video_focus_e2e"

    def _chunk(cid: str, resource_id: str, source_type: str, text: str, **kw):
        c = make_chunk(cid, resource_id, source_type, text, **kw)
        # File-ingested video: no YouTube id, matched by resource_id only.
        if source_type == "video":
            c.video_id = None
        return c

    # 12 PDF chunks that each outrank the single video chunk on raw cosine,
    # so only a working focus rank can put the video first.
    pdf_chunks = [
        _chunk(
            f"p_{i}",
            pdf_resource,
            "pdf",
            "partial derivative chapter worked example lecture notes",
            page_no=i + 1,
        )
        for i in range(12)
    ]
    video_chunk = _chunk(
        "v_focus_e2e",
        video_resource,
        "video",
        "a partial derivative is the rate of change of a multivariable function",
        start_ms=30_000,
        end_ms=80_000,
    )
    store = InMemoryVectorStore([*pdf_chunks, video_chunk])
    set_service_override(ChatService(store=store, embedder=EMBEDDER, llm=BunchedLLM()))

    sid, token = create_session(
        client,
        resource_scope=[video_resource, pdf_resource],
        resource_id=video_resource,
        current_time_ms=120_000,
    )
    r = post_chat(
        client,
        sid,
        token,
        video_id=video_resource,
        current_time_ms=120_000,
        message="What are partial derivatives?",
    )
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["refused"] is False
    # The bunched video marker must survive extraction as citations...
    assert "v_focus_e2e" in [c["chunk_id"] for c in body["citations"]]
    assert any(c["source_type"] == "video" for c in body["citations"]), body[
        "citations"
    ]
    # ...and must not survive into the visible answer.
    assert "chunk_id" not in body["answer"]
    assert "partial derivative is similar" in body["answer"]


# ---------------------------------------------------------------------------
# Session persistence across restarts (JSON state file).
# Persistence is off by default so the suite never touches apps/api/data/;
# these tests turn it on against a tmp_path, mirroring how test_batch_ingest
# monkeypatches INGEST_STATE_PATH.
# ---------------------------------------------------------------------------


def test_sessions_survive_reload(client: TestClient, monkeypatch, tmp_path):
    """A session created before a restart must still resolve after reload."""
    from app.chat import sessions as sessions_mod

    monkeypatch.setattr(
        sessions_mod, "SESSION_STATE_PATH", tmp_path / "chat_sessions.json"
    )
    monkeypatch.setattr(sessions_mod, "_persistence_enabled", True)

    sid, token = create_session(client)
    assert (tmp_path / "chat_sessions.json").is_file()

    # Simulate the restart: fresh stores, reload only from the file.
    fresh_sessions = sessions_mod.InMemorySessionStore()
    fresh_messages = sessions_mod.InMemoryMessageStore()
    monkeypatch.setattr(sessions_mod, "session_store", fresh_sessions)
    monkeypatch.setattr(sessions_mod, "message_store", fresh_messages)
    monkeypatch.setattr(chat_router, "session_store", fresh_sessions)
    monkeypatch.setattr(chat_router, "message_store", fresh_messages)
    try:
        assert sessions_mod.load_session_state() == 1
        restored = fresh_sessions.get(sid)
        assert restored is not None
        assert restored.learner_id == "learner1"
        assert restored.course_id == "course1"
        assert restored.current_time_ms == T
        # Resolvable end-to-end, not just present in the dict.
        r = client.get(f"/sessions/{sid}/messages?session_token={token}")
        assert r.status_code == 200, r.text
    finally:
        sessions_mod.set_persistence_enabled(False)


def test_unknown_session_still_404s(client: TestClient, monkeypatch, tmp_path):
    """Restore must not invent sessions: an absent id still 404s."""
    from app.chat import sessions as sessions_mod

    monkeypatch.setattr(
        sessions_mod, "SESSION_STATE_PATH", tmp_path / "chat_sessions.json"
    )
    monkeypatch.setattr(sessions_mod, "_persistence_enabled", True)
    sid, _token = create_session(client)

    fresh_sessions = sessions_mod.InMemorySessionStore()
    monkeypatch.setattr(sessions_mod, "session_store", fresh_sessions)
    monkeypatch.setattr(chat_router, "session_store", fresh_sessions)
    try:
        assert sessions_mod.load_session_state() == 1
        r = post_chat(client, "ses_definitely_missing", "token")
        assert r.status_code == 404, r.text
        assert r.json()["detail"] == "session not found"
        # The real one still works; only the unknown id is refused.
        assert fresh_sessions.get(sid) is not None
        assert fresh_sessions.get("ses_definitely_missing") is None
    finally:
        sessions_mod.set_persistence_enabled(False)


def test_messages_persist_with_session(client: TestClient, monkeypatch, tmp_path):
    """Messages appended before a reload are retrievable after it."""
    from app.chat import sessions as sessions_mod

    monkeypatch.setattr(
        sessions_mod, "SESSION_STATE_PATH", tmp_path / "chat_sessions.json"
    )
    monkeypatch.setattr(sessions_mod, "_persistence_enabled", True)

    sid, token = create_session(client)
    assert post_chat(client, sid, token).status_code == 200

    fresh_sessions = sessions_mod.InMemorySessionStore()
    fresh_messages = sessions_mod.InMemoryMessageStore()
    # monkeypatch, NOT bare assignment: a bare `sessions_mod.session_store = ...`
    # survives teardown and permanently rebinds the module global. The router
    # keeps its own reference to the *original* object (restored by
    # monkeypatch), so every later test creates its session in one store while
    # ChatService reads from the other -> /chat 404s "session not found"
    # depending on collection order.
    monkeypatch.setattr(sessions_mod, "session_store", fresh_sessions)
    monkeypatch.setattr(sessions_mod, "message_store", fresh_messages)
    monkeypatch.setattr(chat_router, "session_store", fresh_sessions)
    monkeypatch.setattr(chat_router, "message_store", fresh_messages)
    try:
        sessions_mod.load_session_state()
        msgs = fresh_messages.list_for_session(sid)
        assert [m.role for m in msgs] == ["user", "assistant"]
        assert fresh_messages.user_texts(sid) == [msgs[0].content]
        r = client.get(f"/sessions/{sid}/messages?session_token={token}")
        assert r.status_code == 200, r.text
        assert [m["role"] for m in r.json()["messages"]] == ["user", "assistant"]
    finally:
        sessions_mod.set_persistence_enabled(False)


# --- Follow-up classification: linguistic signals, not length -------------
#
# Regression: the old rule prefixed ANY message of <=8 words with the prior
# turn, so "what is f(x,y)" became "is this partial derivatives? what is
# f(x,y)". Retrieval then chased the *previous* question and answered both in
# one turn, citing the wrong chunk.


def test_short_selfcontained_question_is_not_rewritten():
    history = ["is this partial derivatives?"]
    out = rewrite_query("what is f(x,y)", history)
    assert out == "what is f(x,y)"
    assert "partial derivatives" not in out


def test_symbolic_question_is_never_rewritten():
    history = ["what is f(x,y)", "is this partial derivatives?"]
    for msg in ("what is cos(y)", "what is dy", "what is sin(y)"):
        out = rewrite_query(msg, history)
        assert out == msg, f"{msg!r} was rewritten to {out!r}"


def test_pronoun_followup_is_rewritten():
    history = ["what is f(x,y)"]
    for msg in ("what about that", "and why"):
        out = rewrite_query(msg, history)
        assert "what is f(x,y)" in out
        assert msg in out
    # an anaphoric pronoun is a genuine follow-up signal
    assert "what is f(x,y)" in rewrite_query("explain it", history)
    # a *descriptive* demonstrative is not
    assert rewrite_query("what is that yellow function", history) == (
        "what is that yellow function"
    )


def test_empty_history_returns_message():
    for msg in ("what about that", "what is f(x,y)", "and why"):
        assert rewrite_query(msg, []) == msg
        assert rewrite_query(msg, ["", "   "]) == msg


def test_rewrite_never_widens_scope(monkeypatch):
    """Rewriting changes the retrieval query only -- never scope, course or T.

    Drives ``ChatService.chat`` directly with private session/message stores
    so the assertion cannot be perturbed by module-level store state leaked
    from other tests.
    """
    import asyncio

    from app.chat.sessions import InMemoryMessageStore, InMemorySessionStore
    from app.retrieval.service import RetrievalService

    calls: list[dict] = []
    real_retrieve = RetrievalService.retrieve

    async def spy(self, query, course_id, scope, t_ms, *args, **kwargs):
        calls.append(
            {
                "query": query,
                "course_id": course_id,
                "scope": list(scope),
                "t_ms": t_ms,
                "kwargs": dict(kwargs),
            }
        )
        return await real_retrieve(self, query, course_id, scope, t_ms, *args, **kwargs)

    monkeypatch.setattr(RetrievalService, "retrieve", spy)

    sessions = InMemorySessionStore()
    messages = InMemoryMessageStore()
    service = ChatService(
        store=fixture_store(),
        embedder=EMBEDDER,
        llm=DeterministicStubLLM(),
        sessions=sessions,
        messages=messages,
    )
    session = sessions.create(
        learner_id="learner1",
        course_id="course1",
        resource_id="vid",
        lesson_id="lesson1",
        resource_scope=["vid", "handout"],
        current_time_ms=T,
    )

    async def run() -> None:
        await service.chat(
            session_id=session.id,
            current_time_ms=T,
            message="what is photosynthesis light chemical energy",
        )
        await service.chat(
            session_id=session.id, current_time_ms=T, message="what is f(x,y)"
        )

    asyncio.run(run())

    assert len(calls) == 2
    first, second = calls
    # scope / course / gate are byte-identical; only the query string differs
    assert second["scope"] == first["scope"] == ["vid", "handout"]
    assert second["course_id"] == first["course_id"] == "course1"
    assert second["t_ms"] == first["t_ms"] == T
    assert second["kwargs"] == first["kwargs"]
    # the standalone message was not prefixed by the prior turn
    assert second["query"] == "what is f(x,y)"


# --- overlapping-citation behaviour ---------------------------------------


def _overlap_store() -> InMemoryVectorStore:
    """Three sliding-window speech chunks covering the SAME passage."""
    return InMemoryVectorStore(
        [
            make_chunk(
                "c_w1",
                "vid",
                "video_transcript",
                "dy is the change in the y direction of the output",
                4_180,
                4_530,
            ),
            make_chunk(
                "c_w2",
                "vid",
                "video_transcript",
                "dy is the change in the y direction here",
                4_330,
                5_060,
            ),
            make_chunk(
                "c_w3",
                "vid",
                "video_transcript",
                "the change in the y direction, that is dy",
                4_530,
                5_230,
            ),
        ]
    )


def test_overlapping_citations_are_all_returned(client: TestClient):
    """Three heavily-overlapping same-resource windows -> three citations."""

    class OverlapLLM:
        async def complete(self, *, system, user_prompt, passages, mode):
            ids = [p.chunk_id for p in passages]
            return LLMResult(
                text="dy is the change in the y direction. "
                + " ".join(f"[chunk_id={i}]" for i in ids),
                cited_ids=list(ids),
            )

    store = _overlap_store()
    set_service_override(ChatService(store=store, embedder=EMBEDDER, llm=OverlapLLM()))
    sid, token = create_session(client, current_time_ms=6_000)
    r = post_chat(client, sid, token, current_time_ms=6_000)
    assert r.status_code == 200
    body = r.json()
    assert body["refused"] is False
    # No merging: every cited chunk is surfaced with its own original span.
    assert len(body["citations"]) == 3
    spans = {c["chunk_id"]: (c["start_ms"], c["end_ms"]) for c in body["citations"]}
    assert spans == {
        "c_w1": (4_180, 4_530),
        "c_w2": (4_330, 5_060),
        "c_w3": (4_530, 5_230),
    }


def test_distinct_resource_citations_are_returned_separately(client: TestClient):
    """Identical intervals on different resources are NOT duplicates."""

    class TwoResourceLLM:
        async def complete(self, *, system, user_prompt, passages, mode):
            ids = [p.chunk_id for p in passages]
            return LLMResult(
                text="The video and the handout agree. "
                + " ".join(f"[chunk_id={i}]" for i in ids),
                cited_ids=list(ids),
            )

    video = make_chunk(
        "c_v", "vid2", "video_transcript", "dy is a change in the y direction", 10, 900
    )
    other_video = make_chunk(
        "c_v2",
        "vid3",
        "video_transcript",
        "dy is a change in the y direction again",
        10,
        900,
    )
    set_service_override(
        ChatService(
            store=InMemoryVectorStore([video, other_video]),
            embedder=EMBEDDER,
            llm=TwoResourceLLM(),
        )
    )
    sid, token = create_session(
        client,
        resource_id="vid2",
        resource_scope=["vid2", "vid3"],
        current_time_ms=2_000,
    )
    r = post_chat(
        client,
        sid,
        token,
        current_time_ms=2_000,
        message="what is dy in the y direction",
    )
    assert r.status_code == 200
    body = r.json()
    assert body["refused"] is False
    assert len(body["citations"]) == 2
    assert {c["chunk_id"] for c in body["citations"]} == {"c_v", "c_v2"}


def test_unknown_id_rejected_independent_of_citation_merging(client: TestClient):
    """An unknown id alongside valid overlapping ids must still refuse."""

    class EvilOverlapLLM:
        async def complete(self, *, system, user_prompt, passages, mode):
            ids = [p.chunk_id for p in passages]
            return LLMResult(
                text="dy is the change in the y direction. "
                + " ".join(f"[chunk_id={i}]" for i in ids)
                + " [chunk_id=not_a_real_chunk]",
                cited_ids=ids + ["not_a_real_chunk"],
            )

    set_service_override(
        ChatService(store=_overlap_store(), embedder=EMBEDDER, llm=EvilOverlapLLM())
    )
    sid, token = create_session(client, current_time_ms=6_000)
    r = post_chat(client, sid, token, current_time_ms=6_000)
    assert r.status_code == 200
    body = r.json()
    assert body["refused"] is True
    assert body["citations"] == []
    assert body["answer"] == REFUSAL_TEXT
