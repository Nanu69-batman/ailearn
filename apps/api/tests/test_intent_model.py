"""Intent routing: regex first, a small model for undecided messages only."""

from __future__ import annotations

import asyncio
import time

import pytest

from app.chat.intent import OVERVIEW, SMALLTALK, STUDY_QUESTIONS
from app.chat.intent_model import ModelIntentRouter, get_intent_router, worth_asking


class FakeRouter(ModelIntentRouter):
    def __init__(self, label="chat", confidence=0.95, delay=0.0, error=None, **kw):
        super().__init__(api_key="test", model="fake", **kw)
        self.reply = (label, confidence)
        self.delay = delay
        self.error = error
        self.calls: list[str] = []

    def _ask(self, message):
        self.calls.append(message)
        if self.delay:
            time.sleep(self.delay)
        if self.error:
            raise self.error
        return self.reply


def route(router, message):
    return asyncio.run(router.route(message))


def test_regex_decides_clear_cases_without_the_model():
    router = FakeRouter(label="concept")
    assert route(router, "hi") == SMALLTALK
    assert route(router, "who are you") == SMALLTALK
    assert route(router, "quiz me") == STUDY_QUESTIONS
    assert router.calls == []


def test_plain_subject_question_never_reaches_the_model():
    router = FakeRouter(label="chat")
    assert route(router, "what is a partial derivative") is None
    assert router.calls == []


@pytest.mark.parametrize(
    "message", ["yo what's your deal", "can u make flashcards?", "do you remember things"]
)
def test_undecided_message_about_the_assistant_is_routed_by_the_model(message):
    router = FakeRouter(label="chat", confidence=0.95)
    assert route(router, message) == SMALLTALK
    assert router.calls == [message]


def test_low_confidence_chat_takes_the_grounded_path():
    assert route(FakeRouter(label="chat", confidence=0.6), "can u make flashcards?") is None


def test_concept_label_takes_the_grounded_path():
    assert route(FakeRouter(label="concept"), "can you go over limits again") is None


def test_model_can_route_to_overview():
    assert route(FakeRouter(label="overview"), "where are we at with you") == OVERVIEW


def test_timeout_and_errors_fall_back_to_the_grounded_path():
    slow = FakeRouter(label="chat", delay=0.5, timeout_s=0.05)
    assert route(slow, "can u make flashcards?") is None
    broken = FakeRouter(error=RuntimeError("quota"))
    assert route(broken, "can u make flashcards?") is None


def test_answers_are_cached_per_message():
    router = FakeRouter(label="chat")
    route(router, "can u make flashcards?")
    route(router, "Can U  make flashcards?")
    assert len(router.calls) == 1


def test_worth_asking():
    assert worth_asking("what can your app do for exams")
    assert worth_asking("lol")
    assert not worth_asking("explain the chain rule for partial derivatives")
    assert not worth_asking("you " + "x" * 300)


def test_router_off_without_key_or_when_disabled(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "llm_api_key", "")
    monkeypatch.setattr(settings, "intent_router_enabled", True)
    assert get_intent_router() is None
    monkeypatch.setattr(settings, "llm_api_key", "k")
    monkeypatch.setattr(settings, "intent_router_enabled", False)
    assert get_intent_router() is None
