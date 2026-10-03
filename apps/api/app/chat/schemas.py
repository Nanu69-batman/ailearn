"""Pydantic v2 schemas for the Phase 4 chat API."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

#: Server-known learner-clock ceiling: current_time_ms must lie in [0, 6h].
#: Bounds the client-controlled timestamp T (H3) so a caller cannot unlock
#: arbitrary future material by sending T=end.
MAX_CURRENT_TIME_MS = 6 * 3600 * 1000


class ChatMode(str, Enum):
    qa = "qa"
    explain_simple = "explain_simple"
    analogy = "analogy"
    summarize = "summarize"
    compare = "compare"
    quiz = "quiz"


class LmsSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    learner_id: str = Field(min_length=1, max_length=256)
    course_id: str = Field(min_length=1, max_length=256)
    resource_id: str = Field(min_length=1, max_length=256)
    lesson_id: str | None = Field(default=None, max_length=256)
    resource_scope: list[str] = Field(min_length=1, max_length=256)
    current_time_ms: int = Field(ge=0, le=MAX_CURRENT_TIME_MS)
    signature: str | None = Field(default=None, max_length=512)
    issued_at: int = Field(
        description="Epoch ms (or epoch seconds) when launch was issued"
    )


class LmsSessionResponse(BaseModel):
    session_id: str
    learner_id: str
    course_id: str
    #: HMAC binding over (session_id, learner_id); echo per /chat + history call.
    session_token: str


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1, max_length=256)
    #: HMAC session binding issued at /lms/session; verified per request (401).
    session_token: str | None = Field(default=None, max_length=512)
    video_id: str | None = Field(default=None, max_length=256)
    current_time_ms: int = Field(ge=0, le=MAX_CURRENT_TIME_MS)
    message: str = Field(min_length=1, max_length=4000)
    mode: ChatMode = Field(default=ChatMode.qa)


class Citation(BaseModel):
    chunk_id: str
    source_type: str | None = None
    video_id: str | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    page_no: int | None = None
    slide_no: int | None = None


class ChatResponse(BaseModel):
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    refused: bool = False


class MessageOut(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str
    citations: list[Citation] = Field(default_factory=list)
    created_at: str


class MessagesResponse(BaseModel):
    session_id: str
    messages: list[MessageOut]
