"""Pydantic v2 schemas for the Phase 4 chat API."""

from __future__ import annotations

from enum import Enum
from typing import Literal

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


#: What kind of signal a citation was drawn from, for material where that
#: distinction is meaningful (i.e. video). A Whisper transcript chunk and a
#: sampled-frame vision chunk BOTH carry ``source_type == "video"`` and are
#: indistinguishable to a learner without this field.
#:
#: ``transcript`` -> spoken audio (Whisper / YouTube captions).
#: ``visual``     -> a sampled video frame described by the vision model.
CitationModality = Literal["transcript", "visual"]

#: Chunk metadata ``kind`` -> public modality. Deliberately NOT exhaustive and
#: deliberately NOT defaulted: ``document`` (PDF/slide text) is not a video
#: modality at all, so it has no honest value here and maps to None.
_KIND_TO_MODALITY: dict[str, CitationModality] = {
    "transcript": "transcript",
    "caption": "transcript",
    "visual": "visual",
}


def normalize_modality(kind: str | None) -> CitationModality | None:
    """Map a stored chunk ``kind`` to the public modality vocabulary.

    Returns ``None`` for a missing, empty, or unrecognised ``kind``. That is
    the honest answer: we do not know how the evidence was produced, so we say
    nothing rather than assert "transcript". Callers fall back to their
    neutral display when this is None.
    """
    if not kind:
        return None
    return _KIND_TO_MODALITY.get(str(kind).strip().lower())


class Citation(BaseModel):
    chunk_id: str
    source_type: str | None = None
    #: "transcript" | "visual" | None. None means the chunk carried no
    #: recognised ``kind`` -- NOT "it was a transcript". Additive field; every
    #: pre-existing response shape is unchanged when it is absent.
    modality: CitationModality | None = None
    video_id: str | None = None
    start_ms: int | None = None
    end_ms: int | None = None
    page_no: int | None = None
    slide_no: int | None = None


class ChatResponse(BaseModel):
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    refused: bool = False
    #: True only when the session grant, intersected with the focused
    #: resource, is EMPTY -- i.e. the session does not cover the material
    #: being viewed (stale grant, deleted resource, or a different resource).
    #: Lets the client tell a session-grant problem apart from a genuine
    #: low-evidence refusal. Defaults to False so every existing response
    #: shape and assertion is unchanged.
    scope_empty: bool = False


class MessageOut(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str
    citations: list[Citation] = Field(default_factory=list)
    created_at: str


class MessagesResponse(BaseModel):
    session_id: str
    messages: list[MessageOut]
