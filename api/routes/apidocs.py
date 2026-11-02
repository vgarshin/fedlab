"""API keys for outside services + the API reference page (admin only).

The reference is generated from the live OpenAPI schema, enriched with the role each endpoint needs
(derived from its dependencies), so it can never drift from the code.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field
from sqlalchemy import select

from api.deps import KEY_PREFIX, Admin, Db, SessionAdmin, hash_key, require_admin, current_user
from core.db import ApiKey, AuditLog

router = APIRouter(prefix="/api/meta", tags=["api keys & reference"])


def key_out(k: ApiKey) -> dict:
    return {"id": k.id, "name": k.name, "prefix": k.prefix, "role": k.role, "created_at": k.created_at, "expires_at": k.expires_at,
            "last_used_at": k.last_used_at, "revoked_at": k.revoked_at}


class KeyCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    role: Literal["viewer", "admin"] = "viewer"
    expires_days: int | None = Field(None, ge=1, le=3650)


@router.get("/keys")
async def list_keys(_: SessionAdmin, db: Db) -> list[dict]:
    """All API keys (hashes are never returned)."""
    return [key_out(k) for k in (await db.execute(select(ApiKey).order_by(ApiKey.id.desc()))).scalars()]


@router.post("/keys", status_code=201)
async def create_key(body: KeyCreate, admin: SessionAdmin, db: Db) -> dict:
    """Create a key. The full key is returned ONCE in `key`; only its hash is stored."""
    key = KEY_PREFIX + secrets.token_urlsafe(32)
    row = ApiKey(name=body.name.strip(), prefix=key[:11], key_hash=hash_key(key), role=body.role, created_by=admin.id,
                 expires_at=datetime.now(timezone.utc) + timedelta(days=body.expires_days) if body.expires_days else None)
    db.add(row)
    await db.flush()
    db.add(AuditLog(user_id=admin.id, action="apikey_create", details={"id": row.id, "name": row.name, "role": row.role}))
    await db.commit()
    return {**key_out(row), "key": key}


@router.post("/keys/{key_id}/revoke")
async def revoke_key(key_id: int, admin: SessionAdmin, db: Db) -> dict:
    row = await db.get(ApiKey, key_id)
    if row is None:
        raise HTTPException(404)
    row.revoked_at = row.revoked_at or datetime.now(timezone.utc)
    db.add(AuditLog(user_id=admin.id, action="apikey_revoke", details={"id": key_id}))
    await db.commit()
    return key_out(row)


def _needs(route: APIRoute) -> str:
    """public | any signed-in user (viewer) | admin | admin session — read from the route's dependencies."""
    calls = set()

    def walk(dep):
        calls.add(dep.call)
        for d in dep.dependencies:
            walk(d)

    walk(route.dependant)
    from api.deps import require_session_admin
    if require_session_admin in calls:
        return "admin (browser login; not by API key)"
    if require_admin in calls:
        return "admin"
    if current_user in calls:
        return "viewer"
    return "public"


def iter_api_routes(routes):
    """All APIRoutes, also those held inside included routers (newer FastAPI includes routers lazily)."""
    for r in routes:
        if isinstance(r, APIRoute):
            yield r
        elif hasattr(r, "original_router"):
            yield from iter_api_routes(r.original_router.routes)


@router.get("/openapi")
async def reference(request: Request, _: Admin) -> dict:
    """The live OpenAPI document with an extra `x-role` on every operation."""
    schema = request.app.openapi()
    roles = {(r.path_format, m.lower()): _needs(r) for r in iter_api_routes(request.app.routes) for m in r.methods}
    for path, ops in schema["paths"].items():
        for method, op in ops.items():
            op["x-role"] = roles.get((path, method), "viewer")
    return schema
