"""/subjects routes — organise materials into a subject retrieval can scope to.

    POST   /subjects
    GET    /subjects
    GET    /subjects/{subject_id}
    PATCH  /subjects/{subject_id}
    DELETE /subjects/{subject_id}
    GET    /subjects/{subject_id}/resources
    POST   /subjects/{subject_id}/resources
    DELETE /subjects/{subject_id}/resources/{resource_id}

A subject owns references to existing resources, not media. ``DELETE
/subjects/{id}`` removes the grouping only — the underlying resources stay in
the retrieval registry and stay ingestible.

Scoping note: the allow-list for a subject comes from
``app.subjects.store.resolve_subject_resource_scope``. It constrains *which
resources* may be read and nothing about time. The strict-T gate
(``end_ms <= T``) is enforced independently by the retrieval layer and is
never relaxed here.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.auth import AuthUser, get_current_user, require_user
from app.classes.store import class_store
from app.config import settings
from app.subjects.schemas import (
    AttachResourceRequest,
    SubjectCreateRequest,
    SubjectListResponse,
    SubjectResponse,
    SubjectUpdateRequest,
)
from app.subjects.store import (
    Subject,
    UnknownResourceError,
    UnknownSubjectError,
    list_subject_resources,
    subject_store,
)

log = logging.getLogger(__name__)

#: Mutating routes need a signed-in user once AUTH_REQUIRED is on.
CurrentUser = Annotated[AuthUser | None, Depends(get_current_user)]
WriteUser = Annotated[AuthUser | None, Depends(require_user)]

router = APIRouter(prefix="/subjects")


def _to_response(subject: Subject, user: AuthUser | None = None) -> SubjectResponse:
    klass = class_store.class_of_subject(subject.id)
    can_edit = subject.owner_id is None or (
        user is not None and user.id == subject.owner_id
    )
    return SubjectResponse(
        id=subject.id,
        name=subject.name,
        description=subject.description,
        course_id=subject.course_id,
        resource_count=len(subject.resource_ids),
        resource_ids=list(subject.resource_ids),
        created_at=subject.created_at,
        class_id=klass.id if klass else None,
        class_name=klass.name if klass else None,
        can_edit=can_edit,
    )


def _visible(subject: Subject, user: AuthUser | None) -> bool:
    """Public (no owner) subjects are visible to all; owned ones to the owner
    and to students of a class the subject belongs to."""
    if subject.owner_id is None:
        return True
    if user is None:
        return False
    return user.id == subject.owner_id or class_store.student_can_read_subject(
        user.id, subject.id
    )


def _require(subject_id: str, user: AuthUser | None) -> Subject:
    """The subject, or 404 when unknown or owned by someone else."""
    subject = subject_store.get(subject_id)
    if subject is None or not _visible(subject, user):
        raise HTTPException(status_code=404, detail=f"unknown subject {subject_id}")
    return subject


def _require_writable(subject_id: str, user: AuthUser | None) -> Subject:
    subject = _require(subject_id, user)
    if subject.owner_id is None and user is not None and settings.auth_required:
        raise HTTPException(status_code=403, detail="public subjects are read-only")
    if subject.owner_id is not None and (user is None or user.id != subject.owner_id):
        # A class student can read the teacher's subject but never change it.
        raise HTTPException(status_code=403, detail="only the teacher can change this subject")
    return subject


@router.post("", status_code=201)
async def create_subject(
    body: SubjectCreateRequest, user: WriteUser
) -> SubjectResponse:
    if user is not None and user.role == "student":
        raise HTTPException(
            status_code=403, detail="subjects are managed by your teacher"
        )
    klass = None
    if body.class_id is not None:
        klass = class_store.get(body.class_id)
        if klass is None or user is None or klass.teacher_id != user.id:
            raise HTTPException(status_code=404, detail=f"unknown class {body.class_id}")
    subject = subject_store.create(
        name=body.name,
        description=body.description,
        course_id=body.course_id,
        owner_id=user.id if user is not None else None,
    )
    if klass is not None:
        class_store.add_subject(klass, subject.id)
    return _to_response(subject, user)


@router.get("")
async def list_subjects(user: CurrentUser) -> SubjectListResponse:
    return SubjectListResponse(
        subjects=[
            _to_response(s, user) for s in subject_store.list_all() if _visible(s, user)
        ]
    )


@router.get("/{subject_id}")
async def read_subject(subject_id: str, user: CurrentUser) -> SubjectResponse:
    return _to_response(_require(subject_id, user), user)


@router.patch("/{subject_id}")
async def update_subject(
    subject_id: str, body: SubjectUpdateRequest, user: WriteUser
) -> SubjectResponse:
    _require_writable(subject_id, user)
    subject = subject_store.update(
        subject_id, name=body.name, description=body.description
    )
    if subject is None:
        raise HTTPException(status_code=404, detail=f"unknown subject {subject_id}")
    return _to_response(subject, user)


@router.delete("/{subject_id}")
async def delete_subject(subject_id: str, user: WriteUser) -> dict:
    """Delete the subject. Its resources remain in the registry.

    Only the grouping is removed; indexed material and ingest jobs are
    untouched, so the resources can be attached to another subject later.
    """
    _require_writable(subject_id, user)
    if not subject_store.delete(subject_id):
        raise HTTPException(status_code=404, detail=f"unknown subject {subject_id}")
    class_store.remove_subject(subject_id)
    return {"deleted": subject_id}


@router.get("/{subject_id}/resources")
async def list_attached_resources(subject_id: str, user: CurrentUser) -> dict:
    """The catalogue entries for the resources attached to this subject.

    Same shape as ``GET /courses/{id}/resources`` so the web client renders a
    subject's materials with the component it already uses for a course's, with
    no second lookup and no second title pass.

    A subject is NOT a course. Looking up ``/courses/{subject_id}/resources``
    returned an empty list for every real subject — the two ids are different
    namespaces — and making the client fall back to the subject's
    ``course_id`` would render every subject on that course with identical
    materials. Only the subject's own attachment list is authoritative here.

    404 for an unknown subject; 200 with an empty list for a subject with no
    attachments. Ids that have since vanished from the registry are skipped
    (logged by the store), not fatal.

    Read-only catalogue lookup: no time filtering of any kind. The strict-T gate
    (``end_ms <= T``) is enforced by the retrieval layer and is untouched here.
    """
    _require(subject_id, user)
    resources = list_subject_resources(subject_id)
    if resources is None:
        raise HTTPException(status_code=404, detail=f"unknown subject {subject_id}")
    return {"resources": resources}


@router.post("/{subject_id}/resources")
async def attach_resource(
    subject_id: str, body: AttachResourceRequest, user: WriteUser
) -> SubjectResponse:
    """Attach an existing resource by id.

    404 when the subject is unknown, 422 when the ``resource_id`` does not
    exist in the retrieval registry — a subject must never hold a scope entry
    that resolves to nothing.
    """
    _require_writable(subject_id, user)
    try:
        subject = subject_store.attach_resource(subject_id, body.resource_id)
    except UnknownSubjectError as exc:
        raise HTTPException(
            status_code=404, detail=f"unknown subject {subject_id}"
        ) from exc
    except UnknownResourceError as exc:
        raise HTTPException(
            status_code=422, detail=f"unknown resource {exc}"
        ) from exc
    return _to_response(subject, user)


@router.delete("/{subject_id}/resources/{resource_id}")
async def detach_resource(
    subject_id: str, resource_id: str, user: WriteUser
) -> SubjectResponse:
    _require_writable(subject_id, user)
    subject = subject_store.detach_resource(subject_id, resource_id)
    if subject is None:
        raise HTTPException(status_code=404, detail=f"unknown subject {subject_id}")
    return _to_response(subject, user)