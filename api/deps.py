"""Shared FastAPI dependencies: app context, session auth, roles."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request
from itsdangerous import BadSignature, URLSafeTimedSerializer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.config import Secrets, Settings
import hashlib
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from core.db import ApiKey, User
from core.stores import Stores


@dataclass
class AppState:
    settings: Settings
    secrets: Secrets
    sessions: async_sessionmaker[AsyncSession]
    stores: Stores
    queue: Any            # arq ArqRedis pool (enqueue_job)
    redis: Any            # redis.asyncio client (pub/sub for SSE)
    http: Any             # httpx.AsyncClient for Yandex OAuth calls

    def signer(self, salt: str) -> URLSafeTimedSerializer:
        return URLSafeTimedSerializer(self.secrets.require("app", "session_secret"), salt=salt)


def get_state(request: Request) -> AppState:
    return request.app.state.fedlab


State = Annotated[AppState, Depends(get_state)]


async def get_db(state: State):
    async with state.sessions() as s:
        yield s


Db = Annotated[AsyncSession, Depends(get_db)]


KEY_PREFIX = "fl_"


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def presented_key(request: Request) -> str | None:
    """`Authorization: Bearer fl_...` or `X-API-Key: fl_...`. When a key is presented it is the ONLY credential
    looked at (a cookie is ignored), which is also why such requests need no CSRF header."""
    auth = request.headers.get("authorization", "")
    k = auth[7:].strip() if auth.lower().startswith("bearer ") else request.headers.get("x-api-key", "").strip()
    return k or None


async def user_from_key(db, key: str) -> User:
    row = (await db.execute(select(ApiKey).where(ApiKey.key_hash == hash_key(key)))).scalar_one_or_none()
    now_ = datetime.now(timezone.utc)
    if row is None or row.revoked_at is not None or (row.expires_at is not None and row.expires_at.replace(tzinfo=row.expires_at.tzinfo or timezone.utc) < now_):
        raise HTTPException(401, "Invalid, revoked or expired API key")
    if row.last_used_at is None or now_ - row.last_used_at.replace(tzinfo=row.last_used_at.tzinfo or timezone.utc) > timedelta(minutes=1):
        row.last_used_at = now_                      # throttled: at most one write a minute per key
        await db.commit()
    u = User(id=row.created_by, email=f"api-key:{row.name}", name=f"API key '{row.name}'", role=row.role, active=True)
    u.auth_via = "key"                               # transient object, never added to the session
    return u


async def current_user(request: Request, state: State, db: Db) -> User:
    if key := presented_key(request):
        return await user_from_key(db, key)
    token = request.cookies.get(state.settings.session.cookie_name)
    if not token:
        raise HTTPException(401, "Not authenticated")
    try:
        uid = state.signer("session").loads(token, max_age=state.settings.session.ttl_hours * 3600)
    except BadSignature:
        raise HTTPException(401, "Session expired") from None
    user = await db.get(User, uid)
    if user is None or not user.active:
        raise HTTPException(401, "User is not active")
    user.auth_via = "session"
    return user


CurrentUser = Annotated[User, Depends(current_user)]


async def require_admin(user: CurrentUser) -> User:
    if user.role != "admin":
        raise HTTPException(403, "Admin role required")
    return user


Admin = Annotated[User, Depends(require_admin)]


async def require_session_admin(user: CurrentUser) -> User:
    """Key management needs a logged-in admin: a leaked key must not be able to mint more keys."""
    if user.role != "admin" or getattr(user, "auth_via", "session") == "key":
        raise HTTPException(403, "Admin login required (API keys cannot manage API keys)")
    return user


SessionAdmin = Annotated[User, Depends(require_session_admin)]
