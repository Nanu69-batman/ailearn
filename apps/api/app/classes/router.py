"""/classes routes — teachers run classes, students join them by code.

    GET    /classes                         teacher: own classes; student: joined
    POST   /classes                         teacher
    GET    /classes/join/{code}             preview a class before joining
    POST   /classes/join                    student, {code}
    GET    /classes/{id}                    teacher or member
    PATCH  /classes/{id}                    teacher
    DELETE /classes/{id}                    teacher (subjects stay with the teacher)
    POST   /classes/{id}/code               teacher: new join code (old link stops working)
    DELETE /classes/{id}/members/{user_id}  teacher removes a student, or a student leaves

Subjects are added to a class with ``POST /subjects {class_id}`` (see
``app.subjects.router``); they stay owned by the teacher and are read-only for
students.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.auth import AuthUser, get_current_user
from app.classes.store import Klass, Member, class_store

router = APIRouter(prefix="/classes")


def _signed_in(user: Annotated[AuthUser | None, Depends(get_current_user)]) -> AuthUser:
    if user is None:
        raise HTTPException(status_code=401, detail="sign in required")
    return user


User = Annotated[AuthUser, Depends(_signed_in)]


class ClassCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)


class ClassUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)


class JoinRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=4, max_length=32)


def _subjects(klass: Klass) -> list[dict]:
    from app.subjects.store import subject_store

    out = []
    for sid in klass.subject_ids:
        s = subject_store.get(sid)
        if s is not None:
            out.append(
                {
                    "id": s.id,
                    "name": s.name,
                    "description": s.description,
                    "resource_count": len(s.resource_ids),
                    "created_at": s.created_at,
                }
            )
    return out


def _view(klass: Klass, user: AuthUser) -> dict:
    is_teacher = klass.teacher_id == user.id
    body = {
        "id": klass.id,
        "name": klass.name,
        "description": klass.description,
        "teacher_name": klass.teacher_name,
        "is_teacher": is_teacher,
        "student_count": len(klass.members),
        "subjects": _subjects(klass),
        "created_at": klass.created_at,
    }
    if is_teacher:
        body["join_code"] = klass.join_code
        body["students"] = [
            {"id": m.id, "name": m.name, "email": m.email, "joined_at": m.joined_at}
            for m in klass.members
        ]
    return body


def _require_teacher_role(user: AuthUser) -> None:
    if user.role != "teacher":
        raise HTTPException(status_code=403, detail="only teacher accounts can do this")


def _readable(class_id: str, user: AuthUser) -> Klass:
    klass = class_store.get(class_id)
    if klass is None or (
        klass.teacher_id != user.id and user.id not in klass.member_ids()
    ):
        raise HTTPException(status_code=404, detail=f"unknown class {class_id}")
    return klass


def _owned(class_id: str, user: AuthUser) -> Klass:
    klass = _readable(class_id, user)
    if klass.teacher_id != user.id:
        raise HTTPException(status_code=403, detail="only the class teacher can do this")
    return klass


@router.get("")
async def list_classes(user: User) -> dict:
    classes = (
        class_store.for_teacher(user.id)
        if user.role == "teacher"
        else class_store.for_student(user.id)
    )
    return {"role": user.role or "individual", "classes": [_view(c, user) for c in classes]}


@router.post("", status_code=201)
async def create_class(body: ClassCreateRequest, user: User) -> dict:
    _require_teacher_role(user)
    klass = class_store.create(
        name=body.name.strip(),
        description=body.description.strip(),
        teacher_id=user.id,
        teacher_name=user.name or user.email,
    )
    return _view(klass, user)


@router.get("/join/{code}")
async def preview_join(code: str, user: User) -> dict:
    klass = class_store.by_code(code)
    if klass is None:
        raise HTTPException(status_code=404, detail="this invite link is invalid or has expired")
    return {
        "id": klass.id,
        "name": klass.name,
        "description": klass.description,
        "teacher_name": klass.teacher_name,
        "student_count": len(klass.members),
        "is_teacher": klass.teacher_id == user.id,
        "already_member": user.id in klass.member_ids(),
    }


@router.post("/join")
async def join_class(body: JoinRequest, user: User) -> dict:
    klass = class_store.by_code(body.code)
    if klass is None:
        raise HTTPException(status_code=404, detail="this invite link is invalid or has expired")
    if klass.teacher_id == user.id:
        raise HTTPException(status_code=409, detail="you teach this class")
    if user.role != "student":
        raise HTTPException(status_code=403, detail="only student accounts can join a class")
    class_store.add_member(klass, Member(id=user.id, name=user.name, email=user.email))
    return _view(klass, user)


@router.get("/{class_id}")
async def read_class(class_id: str, user: User) -> dict:
    return _view(_readable(class_id, user), user)


@router.patch("/{class_id}")
async def update_class(class_id: str, body: ClassUpdateRequest, user: User) -> dict:
    klass = _owned(class_id, user)
    return _view(class_store.update(klass, name=body.name, description=body.description), user)


@router.delete("/{class_id}")
async def delete_class(class_id: str, user: User) -> dict:
    _owned(class_id, user)
    class_store.delete(class_id)
    return {"deleted": class_id}


@router.post("/{class_id}/code")
async def regenerate_code(class_id: str, user: User) -> dict:
    klass = _owned(class_id, user)
    return _view(class_store.regenerate_code(klass), user)


@router.delete("/{class_id}/members/{member_id}")
async def remove_member(class_id: str, member_id: str, user: User) -> dict:
    klass = _readable(class_id, user)
    if klass.teacher_id != user.id and member_id != user.id:
        raise HTTPException(status_code=403, detail="only the class teacher can remove students")
    class_store.remove_member(klass, member_id)
    return {"removed": member_id}
