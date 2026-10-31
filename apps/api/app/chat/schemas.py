"""Pydantic v2 schemas for the Phase 4 chat API."""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal

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


class Personalization(BaseModel):
    """The learner's saved preferences (Malai personalization). Style only."""

    model_config = ConfigDict(extra="ignore")

    name: str | None = Field(default=None, max_length=60)
    about: str | None = Field(default=None, max_length=300)
    traits: list[Annotated[str, Field(max_length=40)]] = Field(
        default_factory=list, max_length=8
    )
    instructions: str | None = Field(default=None, max_length=1500)
    language: Literal["auto", "english", "hinglish", "hindi"] = "auto"
    length: Literal["concise", "balanced", "detailed"] = "balanced"


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1, max_length=256)
    #: HMAC session binding issued at /lms/session; verified per request (401).
    video_id: str | None = Field(default=None, max_length=256)
    current_time_ms: int = Field(ge=0, le=MAX_CURRENT_TIME_MS)
    message: str = Field(min_length=1, max_length=4000)
    mode: ChatMode = Field(default=ChatMode.qa)
    #: Which of the session's chats this message belongs to; omitted means the
    #: session's original chat ("main").
    thread_id: str | None = Field(default=None, max_length=64)
    #: Materials the learner marked done. Besides the open material
    #: (``video_id``), these are readable in full. Intersected with the
    #: session grant server-side: never a way to read an ungranted resource.
    completed_resource_ids: list[Annotated[str, Field(max_length=64)]] = Field(
        default_factory=list, max_length=100
    )
    #: Narrows the session grant to these materials (e.g. one subject's), for a
    #: chat that is not tied to one open material. Intersected with the grant
    #: server-side: it can only ever remove materials, never add one.
    scope_resource_ids: list[Annotated[str, Field(max_length=64)]] | None = Field(
        default=None, max_length=256
    )
    #: A chat that belongs to no subject (Malai's general chat). It answers
    #: from general knowledge and only searches the learner's materials when
    #: the message explicitly asks about them.
    general: bool = False
    #: The chat's subject's other materials (not readable in this chat). When
    #: the answer is refused or not covered, they are searched and matching ids
    #: come back as ``suggested_resource_ids`` (see app/chat/suggest.py).
    #: Intersected with the session grant: never a way to read anything.
    suggest_resource_ids: list[Annotated[str, Field(max_length=64)]] = Field(
        default_factory=list, max_length=256
    )
    #: A subject chat: when the readable materials do not cover a study
    #: question, do not answer from general knowledge unasked. Ask instead:
    #: access to a material that covers it (``suggested_resource_ids``), or
    #: an answer from general knowledge (``offer_general``). Sending the same
    #: question again without this flag is the learner's "yes".
    ask_first: bool = False
    #: Materials the message refers to by name or kind ("the Pravin Chandra
    #: pdf", "the video"), resolved by the client from the subject's list.
    #: None readable here -> ask for access straight away; some readable ->
    #: the answer is drawn from those only. Intersected with the session
    #: grant like every other id list: it can narrow, never widen.
    referenced_resource_ids: list[Annotated[str, Field(max_length=64)]] = Field(
        default_factory=list, max_length=64
    )
    #: The learner's personalization, applied as a style note to the prompt.
    personalization: Personalization | None = None


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
    #: 1-based position of an image within its resource, for
    #: ``source_type == "image"`` citations. ``None`` for every other source
    #: type. An image citation deliberately carries no ``start_ms``/
    #: ``end_ms``: an uploaded image is ungated and has no moment in time, so
    #: a timestamp here would be a fabricated reference point.
    image_no: int | None = None
    #: The cited passage itself, so the client can highlight it in the PDF or
    #: show it beside the video. Only passages that already passed the
    #: strict-T gate are ever cited, so this reveals nothing ahead of T.
    text: str | None = None
    #: True ONLY for a live capture -- the exact frame the learner's player was
    #: showing at ``current_time_ms``, described on demand (see
    #: ``app.ingestion.liveframe``). Every pre-indexed citation is ``False``,
    #: including sampled visual frames, so an older/absent value is read as
    #: "not live" rather than as "unknown provenance".
    #:
    #: A live citation has ``start_ms == end_ms``: the frame is an instant
    #: observation at T, not a span. That is what lets it satisfy the strict-T
    #: gate (``end_ms <= T``) with equality and carry no special-casing in it.
    live: bool = False


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
    #: Materials this chat cannot read that match the question (ids only),
    #: offered so the learner can give the chat access. Empty unless the
    #: request named ``suggest_resource_ids`` and the answer was refused or
    #: not covered by the readable materials.
    suggested_resource_ids: list[str] = Field(default_factory=list)
    #: Ask-first: nothing the learner has covers this; offer to answer from
    #: general knowledge (the client re-sends the question without ask_first).
    offer_general: bool = False
    #: What the live-frame path did for this turn, or ``None`` when no frame was
    #: involved. ``"live"``: the learner's captured frame was described and
    #: injected as high-priority evidence. ``"document"``: a frame was asked for
    #: but this material has none (pdf/pptx/image) -- the answer falls back to
    #: the document's own page/slide text and says so. Absent means "no frame
    #: question", so every pre-existing response shape is unchanged.
    live_frame: Literal["live", "document"] | None = None
    #: Provenance of this answer. ``"grounded"`` (the default, and what every
    #: pre-existing response shape reads as) means the answer was built from
    #: retrieved passages at or before the playhead and validated against them.
    #: ``"supplemental"`` means the lesson had not covered the question, so the
    #: assistant taught it from general knowledge instead -- visibly labelled in
    #: the answer text and carrying ZERO citations, because nothing in it came
    #: from the video. ``"conversational"`` is a greeting / thanks / "who are
    #: you" turn: no retrieval ran, nothing about the lesson is claimed, and it
    #: carries ZERO citations. ``"general"`` is a turn in a chat tied to no
    #: subject: answered from general knowledge, no retrieval, ZERO citations.
    mode: Literal["grounded", "supplemental", "conversational", "general"] = "grounded"
    #: Convenience mirror of ``mode == "grounded"`` for clients that branch on a
    #: boolean. A refusal is NOT grounded (``False``) -- nothing was served from
    #: the lesson -- so this is only ``True`` when a cited answer was returned.
    grounded: bool = True
    #: True only when the focused video has been processed just up to
    #: ``processed_through_ms`` and the playhead is past that point: the
    #: answer is an honest "not processed yet", not a content refusal.
    processing_pending: bool = False
    processed_through_ms: int | None = None
    #: True when the playhead is inside the transcript-processed region but
    #: past ``visual_through_ms``: the transcript is indexed, the on-screen
    #: description for this part is still being produced.
    visual_pending: bool = False
    visual_through_ms: int | None = None


class MessageOut(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str
    citations: list[Citation] = Field(default_factory=list)
    created_at: str


class MessagesResponse(BaseModel):
    session_id: str
    messages: list[MessageOut]
    thread_id: str = "main"


class ThreadOut(BaseModel):
    id: str
    #: The learner's name for it, else the first question asked, else None.
    title: str | None = None
    #: True when ``title`` was set by the learner (not derived).
    renamed: bool = False
    created_at: str
    #: Time of the last message, or ``created_at`` for an empty chat.
    updated_at: str
    message_count: int = 0


class ThreadsResponse(BaseModel):
    session_id: str
    threads: list[ThreadOut]


class ThreadRenameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(max_length=200)
