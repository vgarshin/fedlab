"""Staged deletion of a run with live progress.

Deletion has four stages — input files, work files, exported results, database records — and each
is reported over the run's Redis channel (the same best-effort pipe the ingest progress uses), so
the UI can show one bar per stage. Idempotent by construction: already-empty prefixes list nothing
and missing rows delete nothing, so a failed deletion can simply be started again.
"""
from __future__ import annotations

import time

from sqlalchemy import delete as sa_delete, func, select

from core import events, keys
from core.db import AuditLog, LlmCall, Run, StageItem, Team
from core.pipeline import Ctx, fail_run

STAGES = ("input", "work", "results", "db")
PUBLISH_EVERY = 0.5          # seconds between progress ticks (like the ingest ticker)

_PREFIX = {"input": keys.input_prefix, "work": lambda rid: f"_work/{rid}/", "results": keys.result_prefix}


async def delete_run_with_progress(ctx: Ctx, run_id: int) -> dict:
    """Delete the whole run: objects in both buckets, then its rows in the database.
    Publishes {type: delete, stage, done, total} events per stage and a final
    {type: delete, status: done, objects} event. Raises on failure (after publishing
    {type: delete, status: error}); the caller decides what happens to the run row."""
    removed = 0
    try:
        for stage in ("input", "work"):
            removed += await _delete_prefix(ctx, run_id, stage)
        removed += await _delete_prefix(ctx, run_id, "results")
        removed += await _delete_db(ctx, run_id)
    except Exception as exc:
        await events.publish(ctx.redis, run_id, type="delete", status="error",
                             error=f"{type(exc).__name__}: {exc}")
        raise
    await events.publish(ctx.redis, run_id, type="delete", status="done", objects=removed)
    return {"deleted": True, "objects": removed}


async def delete_run_job(app: Ctx, run_id: int, user_id: int | None = None) -> dict:
    """Body of the worker job (worker.main wraps it). On failure the run is marked 'failed' with the
    reason (the progress events say the same); retrying is always safe: finished stages list empty."""
    try:
        out = await delete_run_with_progress(app, run_id)
    except Exception as exc:
        await fail_run(app, run_id, f"deletion failed: {type(exc).__name__}: {exc}")
        return {"failed": True}
    async with app.sessions() as s:
        s.add(AuditLog(user_id=user_id, action="run_delete_done", details={"run": run_id, "objects": out["objects"]}))
        await s.commit()
    return out


async def _delete_prefix(ctx: Ctx, run_id: int, stage: str) -> int:
    """Delete one bucket prefix (input/, _work/ in the data bucket; <id>/ in the result bucket)."""
    store = ctx.stores.result if stage == "results" else ctx.stores.data
    keys_ = await store.list(_PREFIX[stage](run_id))
    total = len(keys_)
    await events.publish(ctx.redis, run_id, type="delete", stage=stage, done=0, total=total)
    t0 = time.monotonic()
    for i, k in enumerate(keys_, 1):
        await store.delete(k)
        if time.monotonic() - t0 >= PUBLISH_EVERY or i == total:
            await events.publish(ctx.redis, run_id, type="delete", stage=stage, done=i, total=total)
            t0 = time.monotonic()
    return total


async def _delete_db(ctx: Ctx, run_id: int) -> int:
    """Delete the run's rows (llm_calls, stage_items, teams, the run itself) with progress events."""
    async with ctx.sessions() as s:
        counts = {model: (await s.execute(select(func.count()).select_from(model)
                                          .where(model.run_id == run_id))).scalar_one()
                  for model in (LlmCall, StageItem, Team)}
        total = sum(counts.values()) + 1                      # + the run row itself
        await events.publish(ctx.redis, run_id, type="delete", stage="db", done=0, total=total)
        done = 0
        for model, n in counts.items():
            await s.execute(sa_delete(model).where(model.run_id == run_id))
            done += n
            await events.publish(ctx.redis, run_id, type="delete", stage="db", done=done, total=total)
        await s.execute(sa_delete(Run).where(Run.id == run_id))
        await s.commit()
    await events.publish(ctx.redis, run_id, type="delete", stage="db", done=total, total=total)
    return total
