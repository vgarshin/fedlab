"""FastAPI app factory. Run: uvicorn api.main:create_app --factory"""
from __future__ import annotations

from contextlib import asynccontextmanager
from urllib.parse import urlparse

import httpx
import redis.asyncio as aioredis
from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from api.deps import AppState, presented_key
from api.routes import analytics, apidocs, auth, batches, llm, runs, settings as settings_routes, users
from core.config import get_secrets, get_settings
from core import registry
from core import migrate
from core.db import db_url, ensure_run_id_start, make_session_factory
from core.stores import make_stores

UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


def create_app(state: AppState | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if state is not None:                      # tests inject a ready state
            app.state.fedlab = state
            yield
            return
        settings, secrets = get_settings(), get_secrets()
        sessions, engine = make_session_factory(db_url(settings, secrets))
        await migrate.upgrade()
        await registry.seed(sessions, settings)
        await ensure_run_id_start(sessions, settings.run_id_start)
        u = urlparse(settings.redis["url"])
        rs = RedisSettings(host=u.hostname or "redis", port=u.port or 6379)
        http = httpx.AsyncClient(timeout=20)
        app.state.fedlab = AppState(settings, secrets, sessions, make_stores(settings, secrets),
                                    await create_pool(rs), aioredis.from_url(settings.redis["url"]), http)
        yield
        await http.aclose()

    app = FastAPI(title="fedlab", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")

    @app.middleware("http")
    async def csrf_guard(request: Request, call_next):
        # Cookie auth + state-changing request => require a custom header. Browsers cannot add it
        # cross-site without a CORS preflight (which we never allow), so forged forms/images fail.
        if request.method in UNSAFE and request.headers.get("x-requested-with") != "fedlab" and not presented_key(request):
            return JSONResponse({"detail": "Missing X-Requested-With header"}, status_code=403)
        return await call_next(request)

    @app.get("/api/health")
    async def health() -> dict:
        return {"ok": True}

    for r in (auth.router, runs.router, users.router, llm.router, settings_routes.router, analytics.router, apidocs.router, batches.router):
        app.include_router(r)
    return app
