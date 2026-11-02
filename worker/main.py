"""arq worker: asyncio job runner on Redis. Start: ``arq worker.main.WorkerSettings``."""
from __future__ import annotations

import logging
from urllib.parse import urlparse

from arq import Retry
from arq.connections import RedisSettings

from core import pipeline, registry
from core.llm import BudgetExceeded
from core.config import get_secrets, get_settings
from core import migrate
from core.db import db_url, ensure_run_id_start, make_session_factory
from core.stores import make_stores

log = logging.getLogger("fedlab.worker")


async def startup(ctx: dict) -> None:
    settings, secrets = get_settings(), get_secrets()
    sessions, engine = make_session_factory(db_url(settings, secrets))
    await migrate.upgrade()
    await registry.seed(sessions, settings)
    await ensure_run_id_start(sessions, settings.run_id_start)
    ctx["app"] = pipeline.Ctx(sessions, make_stores(settings, secrets), settings, secrets, ctx["redis"])


async def ingest_run_job(ctx: dict, run_id: int) -> dict:
    app: pipeline.Ctx = ctx["app"]
    try:
        return await pipeline.run_ingest(app, run_id)
    except Exception as exc:
        log.exception("ingest of run %s failed", run_id)
        await pipeline.fail_run(app, run_id, f"{type(exc).__name__}: {exc}")
        return {"failed": True}


async def export_run_job(ctx: dict, run_id: int) -> dict:
    return await pipeline.run_export_job(ctx["app"], run_id)


async def team_pipeline_job(ctx: dict, run_id: int, team_id: int) -> dict:
    """'Run all' for one work; the last work to finish triggers the export."""
    app: pipeline.Ctx = ctx["app"]
    out = await pipeline.run_team_pipeline(app, run_id, team_id)
    if await pipeline.all_graded(app, run_id):
        await ctx["redis"].enqueue_job("export_run_job", run_id, _job_id=f"export:{run_id}")   # same id => queued once
    return out


async def stage_item_job(ctx: dict, run_id: int, team_id: int, stage: str) -> dict:
    app: pipeline.Ctx = ctx["app"]
    try:
        return await pipeline.run_team_stage(app, run_id, team_id, stage)
    except Exception as exc:  # bounded retries with backoff, then 'failed' (never blocks other teams)
        log.exception("stage %s team %s failed (try %s)", stage, team_id, ctx["job_try"])
        retryable = not isinstance(exc, (BudgetExceeded, ValueError, FileNotFoundError, pipeline.grading.GradeError))
        if retryable and ctx["job_try"] < app.settings.processing.max_attempts:
            raise Retry(defer=2 ** ctx["job_try"] * 5) from exc
        await pipeline.fail_item(app, run_id, team_id, stage, f"{type(exc).__name__}: {exc}")
        return {"failed": True}


def _redis_settings() -> RedisSettings:
    u = urlparse(get_settings().redis["url"])
    return RedisSettings(host=u.hostname or "redis", port=u.port or 6379)


class WorkerSettings:
    functions = [ingest_run_job, stage_item_job, export_run_job, team_pipeline_job]
    on_startup = startup
    redis_settings = _redis_settings()
    max_jobs = get_settings().processing.worker_concurrency
    max_tries = get_settings().processing.max_attempts
    job_timeout = 1800
    keep_result = 0   # allow re-enqueue with the same job id after completion
