"""Builds a fully local app context (sqlite + directory storage) for tests."""
from __future__ import annotations

import json
from pathlib import Path

from core import pipeline
from core.config import Secrets, Settings
from core.db import Run, create_all, make_session_factory
from core.stores import Stores
from core.storage import LocalStorage

SETTINGS = {
    "env": "dev", "domain": "localhost", "bootstrap_admin_emails": ["admin@example.com"],
    "object_storage": {"endpoint": "http://x", "buckets": {"input": "in", "result": "out"}},
    "oauth": {"redirect_uri": "http://localhost/api/auth/callback"},
    "storage": {"backend": "local"},
}
SECRETS = {"app": {"session_secret": "test-secret", "postgres_password": "x"},
           "yandex_oauth": {"client_id": "cid", "client_secret": "csec"}}


async def make_ctx(tmp: Path, redis=None) -> pipeline.Ctx:
    sessions, engine = make_session_factory(f"sqlite+aiosqlite:///{tmp}/t.db")
    await create_all(engine)
    stores = Stores(LocalStorage(tmp / "s", "in"), LocalStorage(tmp / "s", "out"))
    return pipeline.Ctx(sessions, stores, Settings.model_validate(SETTINGS), Secrets.model_validate(SECRETS), redis)


async def make_run(ctx: pipeline.Ctx, **cfg) -> int:
    async with ctx.sessions() as s:
        run = Run(name="test", config=cfg, source={"kind": "upload"})
        s.add(run)
        await s.commit()
        return run.id
