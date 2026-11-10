"""Staged run deletion: the four stages, their progress events, and the failure/retry behaviour."""
from sqlalchemy import func, select

from api.routes.runs import sse_line
from core import events, run_delete
from core.db import AuditLog, LlmCall, Run, StageItem, Team
from tests.helpers import make_ctx, make_run


def test_sse_line_decodes_bytes_and_emits_keepalive():
    """Redis pub/sub delivers bytes; rendering them raw would produce `data: b'{...}'`, which the
    browser's JSON.parse rejects - and then no progress event (delete, ingest, run status) ever
    reaches the page. The frame must carry plain JSON, and an empty read must be a keepalive."""
    assert sse_line({"data": b'{"type": "delete", "status": "done"}'}) == 'data: {"type": "delete", "status": "done"}\n\n'
    assert sse_line({"data": '{"type": "run", "status": "failed"}'}) == 'data: {"type": "run", "status": "failed"}\n\n'
    assert sse_line(None) == ": keepalive\n\n"


async def _seed(ctx, rid: int) -> None:
    async with ctx.sessions() as s:
        t = Team(run_id=rid, name="A", slug="A")
        s.add(t)
        await s.flush()
        s.add(StageItem(run_id=rid, team_id=t.id, stage="s2_extract", status="done"))
        s.add(LlmCall(run_id=rid, stage="s4_grade", model_id="m1"))
        await s.commit()


async def test_deletion_reports_four_stages_and_removes_everything(tmp_path, monkeypatch):
    ctx = await make_ctx(tmp_path)
    rid = await make_run(ctx)
    await _seed(ctx, rid)
    await ctx.stores.data.put(f"input/{rid}/a.pdf", b"x")
    await ctx.stores.data.put(f"input/{rid}/b.pdf", b"x")
    await ctx.stores.data.put(f"_work/{rid}/A/s2_pages.json", b"x")
    await ctx.stores.result.put(f"{rid}/r.csv", b"x")
    await ctx.stores.data.put("_cache/llm/m/a.json", b"{}")          # shared cache must survive
    log: list[dict] = []

    async def cap(redis, run_id, **ev):
        log.append(ev)

    monkeypatch.setattr(events, "publish", cap)
    out = await run_delete.delete_run_job(ctx, rid, user_id=1)
    assert out == {"deleted": True, "objects": 8}                     # 4 files + 3 child rows + the run row
    stages = list(dict.fromkeys(e["stage"] for e in log if "stage" in e))   # first-appearance order
    assert stages == ["input", "work", "results", "db"]
    totals = {e["stage"]: e["total"] for e in log if "stage" in e and e["done"] == 0}
    assert totals == {"input": 2, "work": 1, "results": 1, "db": 4}   # db: 1 item + 1 call + 1 team + the run
    finals: dict[str, dict] = {}
    for e in log:
        if "stage" in e:
            finals[e["stage"]] = e                                    # last tick per stage
    assert {st: e["done"] for st, e in finals.items()} == {"input": 2, "work": 1, "results": 1, "db": 4}
    assert all(e["done"] == e["total"] for e in finals.values())
    assert log[-1] == {"type": "delete", "status": "done", "objects": 8}
    assert not await ctx.stores.data.exists(f"input/{rid}/a.pdf")
    assert not await ctx.stores.data.exists(f"_work/{rid}/A/s2_pages.json")
    assert not await ctx.stores.result.exists(f"{rid}/r.csv")
    assert await ctx.stores.data.exists("_cache/llm/m/a.json")
    async with ctx.sessions() as s:
        assert await s.get(Run, rid) is None
        for model in (Team, StageItem, LlmCall):
            assert (await s.execute(select(func.count()).select_from(model).where(model.run_id == rid))).scalar_one() == 0
        done = (await s.execute(select(AuditLog).where(AuditLog.action == "run_delete_done"))).scalars().all()
        assert len(done) == 1 and done[0].user_id == 1 and done[0].details == {"run": rid, "objects": 8}


async def test_deletion_failure_marks_run_failed_and_a_retry_finishes(tmp_path, monkeypatch):
    ctx = await make_ctx(tmp_path)
    rid = await make_run(ctx)
    await ctx.stores.data.put(f"input/{rid}/a.pdf", b"x")
    await ctx.stores.data.put(f"input/{rid}/b.pdf", b"x")
    log: list[dict] = []

    async def cap(redis, run_id, **ev):
        log.append(ev)

    monkeypatch.setattr(events, "publish", cap)
    orig = ctx.stores.data.delete
    calls = {"n": 0}

    async def flaky(key):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("s3 down")
        await orig(key)

    ctx.stores.data.delete = flaky
    out = await run_delete.delete_run_job(ctx, rid)
    assert out == {"failed": True}
    assert {"type": "delete", "status": "error", "error": "RuntimeError: s3 down"} in log   # fail_run adds a run event after it
    async with ctx.sessions() as s:
        run = await s.get(Run, rid)
        assert run.status == "failed" and "deletion failed" in run.note
    # retry on healthy storage: b.pdf is still there (the failed delete never ran), a.pdf is skipped
    ctx.stores.data.delete = orig
    out2 = await run_delete.delete_run_job(ctx, rid)
    assert out2 == {"deleted": True, "objects": 2}                    # 1 remaining file + the run row
    assert log[-1] == {"type": "delete", "status": "done", "objects": 2}
    async with ctx.sessions() as s:
        assert await s.get(Run, rid) is None
