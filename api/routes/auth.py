"""Yandex ID (Passport) OAuth, authorization-code flow, handled server-side.

Yandex proves WHO the person is. WHAT they may do comes from our ``users`` table:
a Yandex account that an admin has not added gets "no access".
"""
from __future__ import annotations

import secrets as pysecrets
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select

from api.deps import CurrentUser, Db, State
from core.db import AuditLog, User, now

router = APIRouter(prefix="/api/auth", tags=["auth"])
AUTHORIZE_URL = "https://oauth.yandex.ru/authorize"
TOKEN_URL = "https://oauth.yandex.ru/token"
INFO_URL = "https://login.yandex.ru/info?format=json"
STATE_COOKIE = "fedlab_oauth_state"


def _set_session(resp, state: State, user_id: int) -> None:
    resp.set_cookie(
        state.settings.session.cookie_name, state.signer("session").dumps(user_id),
        max_age=state.settings.session.ttl_hours * 3600, httponly=True,
        secure=state.settings.env != "dev", samesite="lax", path="/",
    )


@router.get("/login")
async def login(state: State) -> RedirectResponse:
    nonce = pysecrets.token_urlsafe(24)
    q = urlencode({
        "response_type": "code", "client_id": state.secrets.require("yandex_oauth", "client_id"),
        "redirect_uri": state.settings.oauth.redirect_uri, "state": nonce,
        "scope": state.settings.oauth.scopes, "force_confirm": "no",
    })
    resp = RedirectResponse(f"{AUTHORIZE_URL}?{q}")
    resp.set_cookie(STATE_COOKIE, state.signer("oauth-state").dumps(nonce), max_age=600, httponly=True,
                    secure=state.settings.env != "dev", samesite="lax", path="/api/auth")
    return resp


@router.get("/callback")
async def callback(request: Request, state: State, db: Db, code: str = ""):
    # Yandex returns ?code=...&state=...; the query key is literally "state"
    returned = request.query_params.get("state", "")
    cookie = request.cookies.get(STATE_COOKIE, "")
    try:
        expected = state.signer("oauth-state").loads(cookie, max_age=600)
    except Exception:
        raise HTTPException(400, "Login session expired, try again") from None
    if not code or not pysecrets.compare_digest(expected, returned):
        raise HTTPException(400, "Invalid OAuth state")

    tok = await state.http.post(TOKEN_URL, data={
        "grant_type": "authorization_code", "code": code,
        "client_id": state.secrets.require("yandex_oauth", "client_id"),
        "client_secret": state.secrets.require("yandex_oauth", "client_secret"),
    })
    if tok.status_code != 200:
        raise HTTPException(400, "Yandex rejected the login code")
    info = await state.http.get(INFO_URL, headers={"Authorization": f"OAuth {tok.json()['access_token']}"})
    if info.status_code != 200:
        raise HTTPException(400, "Could not read the Yandex profile")
    p = info.json()
    email = (p.get("default_email") or "").lower()
    uid = str(p.get("id", ""))

    user = (await db.execute(select(User).where(User.yandex_uid == uid))).scalar_one_or_none() if uid else None
    if user is None and email:
        user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user is None and email in {e.lower() for e in state.settings.bootstrap_admin_emails}:
        user = User(email=email, role="admin")
        db.add(user)
    if user is None or not user.active:
        db.add(AuditLog(action="login_denied", details={"email": email, "login": p.get("login")}))
        await db.commit()
        return RedirectResponse("/?error=no_access")
    user.yandex_uid, user.login, user.last_login = uid or user.yandex_uid, p.get("login", ""), now()
    user.name = p.get("real_name") or p.get("display_name") or user.name
    await db.flush()  # assigns user.id for a freshly created bootstrap admin
    db.add(AuditLog(user_id=user.id, action="login", details={}))
    await db.commit()
    resp = RedirectResponse("/")
    _set_session(resp, state, user.id)
    resp.delete_cookie(STATE_COOKIE, path="/api/auth")
    return resp


@router.get("/dev-login")
async def dev_login(state: State, db: Db, email: str):
    """Only when settings.env == 'dev'. Never reachable in production."""
    if state.settings.env != "dev":
        raise HTTPException(404)
    user = (await db.execute(select(User).where(User.email == email.lower()))).scalar_one_or_none()
    if user is None:
        user = User(email=email.lower(), role="admin", login="dev", name="Dev User")
        db.add(user)
        await db.commit()
    resp = RedirectResponse("/")
    _set_session(resp, state, user.id)
    return resp


@router.post("/logout")
async def logout(state: State) -> JSONResponse:
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(state.settings.session.cookie_name, path="/")
    return resp


@router.get("/me")
async def me(user: CurrentUser) -> dict:
    return {"id": user.id, "email": user.email, "login": user.login, "name": user.name, "role": user.role,
            "auth": getattr(user, "auth_via", "session")}
