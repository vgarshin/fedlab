"""Operator tools that act without a browser login (run inside the api container).

    python -m core.admin_tools quick-run --batch demo-unzipped [--name NAME]   create a run from incoming/<batch>/ and press "Run all"
    python -m core.admin_tools delete-run --id 1                               remove a run: database rows + all its objects in both buckets
"""
from __future__ import annotations

import argparse
import asyncio
from urllib.parse import urlparse

from arq import create_pool
from arq.connections import RedisSettings
from sqlalchemy import delete, func, select

from core import keys
from core.config import get_secrets, get_settings
from core.db import AuditLog, LlmCall, Run, StageItem, Team, User, db_url, make_session_factory
from core.stores import make_stores


async def _queue(settings):
    u = urlparse(settings.redis["url"])
    return await create_pool(RedisSettings(host=u.hostname or "redis", port=u.port or 6379))


async def quick_run(batch: str, name: str) -> None:
    settings, secrets = get_settings(), get_secrets()
    sessions, _ = make_session_factory(db_url(settings, secrets))
    queue = await _queue(settings)
    async with sessions() as s:
        admin = (await s.execute(select(User).where(User.role == "admin").order_by(User.id))).scalars().first()
        run = Run(name=name, config={}, created_by=admin.id if admin else None, status="uploaded",
                  source={"kind": "incoming", "prefix": f"{settings.incoming_prefix}{batch}/"})
        s.add(run)
        await s.commit()
        run_id = run.id
    print(f"run {run_id} created from incoming/{batch}/", flush=True)
    await queue.enqueue_job("ingest_run_job", run_id, _job_id=f"ingest:{run_id}")
    for _ in range(200):
        async with sessions() as s:
            st = (await s.get(Run, run_id)).status
        if st in ("ready", "failed"):
            break
        await asyncio.sleep(3)
    print("ingest:", st, flush=True)
    async with sessions() as s:
        ids = (await s.execute(select(Team.id).where(Team.run_id == run_id))).scalars().all()
    for tid in ids:
        await queue.enqueue_job("team_pipeline_job", run_id, tid, _job_id=f"all:{run_id}:{tid}")
    print(f"Run all queued for {len(ids)} works (stages 2-4, then export). Watch run {run_id} in the UI.", flush=True)


async def delete_run(run_id: int) -> None:
    settings, secrets = get_settings(), get_secrets()
    sessions, _ = make_session_factory(db_url(settings, secrets))
    stores = make_stores(settings, secrets)
    n = 0
    for st, prefixes in ((stores.data, (keys.input_prefix(run_id), f"_work/{run_id}/")), (stores.result, (keys.result_prefix(run_id),))):
        for pre in prefixes:
            for k in await st.list(pre):
                await st.delete(k)
                n += 1
    async with sessions() as s:
        for model in (LlmCall, StageItem, Team):
            await s.execute(delete(model).where(model.run_id == run_id))
        await s.execute(delete(Run).where(Run.id == run_id))
        await s.commit()
    print(f"run {run_id} deleted ({n} objects)")


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("quick-run")
    q.add_argument("--batch", required=True)
    q.add_argument("--name", default="Quick run")
    d = sub.add_parser("delete-run")
    d.add_argument("--id", type=int, required=True)
    a = ap.parse_args()
    asyncio.run(quick_run(a.batch, a.name) if a.cmd == "quick-run" else delete_run(a.id))


if __name__ == "__main__":
    main()
