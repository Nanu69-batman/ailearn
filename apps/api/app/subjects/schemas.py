"""Pydantic v2 schemas for the ``/subjects`` API.

Field bounds mirror ``app.chat.schemas``: short opaque ids are length-capped so
a hostile caller cannot push unbounded strings into the persisted state file.
``extra="forbid"`` so a typo'd key is a 422 rather than a silently ignored
field — the same rule that caught the real bugs in HANDOFF §2.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SubjectCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    course_id: str | None = Field(default=None, min_length=1, max_length=256)


class SubjectUpdateRequest(BaseModel):
    """Partial update. Absent fields are left untouched; ``None`` is not a
    valid value for either field, so a client cannot null out a name."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)


class AttachResourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resource_id: str = Field(min_length=1, max_length=256)


class SubjectResponse(BaseModel):
    id: str
    name: str
    description: str
    course_id: str | None = None
    resource_count: int = 0
    resource_ids: list[str] = Field(default_factory=list)
    created_at: str


class SubjectListResponse(BaseModel):
    subjects: list[SubjectResponse]