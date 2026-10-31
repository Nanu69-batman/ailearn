"""A message that names materials ("the pdf", "the video") is answered from
those, or asks for access when the chat cannot read any of them."""

from __future__ import annotations

from app.chat.agent import looks_like_tool_text
from app.chat.router import _apply_references, session_store
from app.chat.schemas import ChatRequest


def _session():
    return session_store.create(
        learner_id="l1", course_id="*", resource_id="res_pdf", lesson_id=None,
        resource_scope=["res_pdf", "res_video"], current_time_ms=0,
    )


def _body(sid: str, **kw) -> ChatRequest:
    return ChatRequest(session_id=sid, current_time_ms=0, message="what does the video say", **kw)


def test_named_material_not_readable_asks_for_access_at_once():
    s = _session()
    early, _body2, locked = _apply_references(_body(
        s.id, ask_first=True, scope_resource_ids=["res_pdf"], referenced_resource_ids=["res_video"],
    ))
    assert early is not None and early.suggested_resource_ids == ["res_video"]
    assert not early.refused and early.citations == []
    assert locked == ["res_video"]


def test_named_readable_material_narrows_the_answer_to_it():
    s = _session()
    early, body, locked = _apply_references(_body(
        s.id, ask_first=True, scope_resource_ids=["res_pdf", "res_video"],
        completed_resource_ids=["res_pdf", "res_video"], referenced_resource_ids=["res_video"],
    ))
    assert early is None and locked == []
    assert body.scope_resource_ids == ["res_video"]
    assert body.completed_resource_ids == ["res_video"]


def test_ungranted_reference_is_ignored():
    s = _session()
    early, body, locked = _apply_references(_body(
        s.id, ask_first=True, scope_resource_ids=["res_pdf"], referenced_resource_ids=["res_secret"],
    ))
    assert early is None and locked == []
    assert body.scope_resource_ids == ["res_pdf"]


def test_without_ask_first_nothing_is_asked():
    s = _session()
    early, _body2, locked = _apply_references(_body(
        s.id, scope_resource_ids=["res_pdf"], referenced_resource_ids=["res_video"],
    ))
    assert early is None and locked == ["res_video"]


def test_tool_call_written_as_text_is_detected():
    assert looks_like_tool_text("search_lesson")
    assert looks_like_tool_text("`get_lesson_content`")
    assert looks_like_tool_text('{"name": "search_lesson", "arguments": {"query": "video"}}')
    assert not looks_like_tool_text("Yes, I can read the video for you.")
    assert not looks_like_tool_text("I looked at search_lesson results and found the definition.")
