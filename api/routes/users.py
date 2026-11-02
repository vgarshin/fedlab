from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from api.deps import Admin, Db
from core.db import AuditLog, User

router = APIRouter(prefix="/api/users", tags=["users"])


class UserCreate(BaseModel):
    email: str
    role: Literal["admin", "viewer"] = "viewer"


class UserPatch(BaseModel):
    role: Literal["admin", "viewer"] | None = None
    active: bool | None = None


def out(u: User) -> dict:
    return {"id": u.id, "email": u.email, "login": u.login, "name": u.name, "role": u.role,
            "active": u.active, "last_login": u.last_login}


@router.get("")
async def list_users(_: Admin, db: Db) -> list[dict]:
    return [out(u) for u in (await db.execute(select(User).order_by(User.email))).scalars()]


@router.post("", status_code=201)
async def add_user(body: UserCreate, admin: Admin, db: Db) -> dict:
    email = body.email.strip().lower()
    if "@" not in email:
        raise HTTPException(400, "Invalid e-mail")
    if (await db.execute(select(User).where(User.email == email))).scalar_one_or_none():
        raise HTTPException(409, "User already exists")
    u = User(email=email, role=body.role)
    db.add(u)
    db.add(AuditLog(user_id=admin.id, action="user_add", details={"email": email, "role": body.role}))
    await db.commit()
    return out(u)


@router.patch("/{user_id}")
async def patch_user(user_id: int, body: UserPatch, admin: Admin, db: Db) -> dict:
    u = await db.get(User, user_id)
    if u is None:
        raise HTTPException(404)
    if u.id == admin.id and (body.active is False or body.role == "viewer"):
        raise HTTPException(400, "You cannot demote or deactivate yourself")
    if body.role is not None:
        u.role = body.role
    if body.active is not None:
        u.active = body.active
    db.add(AuditLog(user_id=admin.id, action="user_patch", details={"user": user_id, **body.model_dump(exclude_none=True)}))
    await db.commit()
    return out(u)
