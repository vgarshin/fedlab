"""Runs, uploads, stage control, team views, live events (SSE)."""
from __future__ import annotations

import asyncio
import json
import re
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from api.deps import Admin, CurrentUser, Db, State
from core import appsettings, events, keys, pipeline
from core.db import AuditLog, LlmCall, ModelEntry, PromptVersion, Run, StageItem, Team
from core.schemas import RunConfig
from core.storage import get_json

router = APIRouter(prefix="/api/runs", tags=["runs"])
ARTIFACT_RE = re.compile(r"^[\w.\-]+\.(json|txt)$")


class RunCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    config: RunConfig = RunConfig()


def run_out(r: Run) -> dict:
    return {"id": r.id, "name": r.name, "status": r.status, "config": r.config, "note": r.note,
            "created_at": r.created_at, "created_by": r.created_by, "source": r.source}


async def get_run(db, run_id: int) -> Run:
    run = await db.get(Run, run_id)
    if run is None:
        raise HTTPException(404, "Run not found")
    return run


async def stage_summary(db, run_id: int) -> dict[str, dict[str, int]]:
    rows = (await db.execute(select(StageItem.stage, StageItem.status, func.count())
                             .where(StageItem.run_id == run_id).group_by(StageItem.stage, StageItem.status))).all()
    out: dict[str, dict[str, int]] = {}
    for stage, status, n in rows:
        out.setdefault(stage, {})[status] = n
    return out


@router.get("")
async def list_runs(_: CurrentUser, db: Db) -> list[dict]:
    runs = (await db.execute(select(Run).order_by(Run.id.desc()))).scalars().all()
    counts = dict((await db.execute(select(Team.run_id, func.count()).group_by(Team.run_id))).all())
    return [{**run_out(r), "teams": counts.get(r.id, 0)} for r in runs]


@router.post("", status_code=201)
async def create_run(body: RunCreate, state: State, user: Admin, db: Db) -> dict:
    cfg = body.config
    run = Run(name=body.name, config=cfg.model_dump(), created_by=user.id)
    db.add(run)
    await db.flush()
    # Run ids restart if the database is ever recreated; never write into a prefix that already holds another run's data.
    for st, pre in ((state.stores.data, keys.input_prefix(run.id)), (state.stores.data, f"_work/{run.id}/"),
                    (state.stores.result, keys.result_prefix(run.id))):
        if await st.list_meta(pre):
            await db.rollback()
            raise HTTPException(409, f"Storage already contains objects under {st.bucket}/{pre} (database reset?). "
                                     "Clean that prefix or restore the database before creating runs.")
    db.add(AuditLog(user_id=user.id, action="run_create", details={"run": run.id, "name": body.name}))
    await db.commit()
    return run_out(run)


async def uses_info(state, db) -> dict:
    """What a stage will use right now (Settings page): default prompt + model per stage."""
    eff = await appsettings.load(state.sessions, state.settings)
    out: dict = {"detect_enabled": eff.detect_enabled}
    for stage, pname, mid in (("grade", eff.defaults.grade_prompt, eff.defaults.grade_model),
                              ("detect", eff.defaults.detect_prompt, eff.defaults.detect_model)):
        try:
            p, m = await appsettings.resolve(db, pname, mid)
            out[stage] = {"prompt": f"{p.name} v{p.version}", "model": m.label or m.id, "ok": True}
        except ValueError as exc:
            out[stage] = {"prompt": pname, "model": mid, "ok": False, "problem": str(exc)}
    return out


@router.get("/{run_id}")
async def get_run_detail(run_id: int, state: State, _: CurrentUser, db: Db) -> dict:
    run = await get_run(db, run_id)
    n = (await db.execute(select(func.count()).select_from(Team).where(Team.run_id == run_id))).scalar_one()
    return {**run_out(run), "teams": n, "stages": await stage_summary(db, run_id), "uses": await uses_info(state, db)}


class ConfigPatch(BaseModel):
    config: RunConfig


@router.put("/{run_id}/config")
async def put_config(run_id: int, body: ConfigPatch, user: Admin, db: Db) -> dict:
    """Per-run options only (OCR, budget cap). Models and prompts come from the Settings page."""
    run = await get_run(db, run_id)
    ensure_not_deleting(run)
    run.config = body.config.model_dump()
    db.add(AuditLog(user_id=user.id, action="run_config", details={"run": run_id}))
    await db.commit()
    return run_out(run)


@router.get("/{run_id}/cost")
async def run_cost(run_id: int, state: State, _: CurrentUser, db: Db) -> dict:
    await get_run(db, run_id)
    eff = await appsettings.load(state.sessions, state.settings)
    rows = (await db.execute(select(LlmCall.stage, LlmCall.model_id, LlmCall.cache_hit, func.count(), func.sum(LlmCall.cost_rub),
                                    func.sum(LlmCall.prompt_tokens), func.sum(LlmCall.completion_tokens))
                             .where(LlmCall.run_id == run_id).group_by(LlmCall.stage, LlmCall.model_id, LlmCall.cache_hit))).all()
    by = [{"stage": st.split(".")[0], "model": m, "cache_hit": h, "calls": n, "cost_rub": round(c or 0, 4),
           "prompt_tokens": pt or 0, "completion_tokens": ct or 0} for st, m, h, n, c, pt, ct in rows]
    return {"spent_rub": round(sum(r["cost_rub"] for r in by), 4), "cap_rub": eff.budget_cap_rub,
            "calls": sum(r["calls"] for r in by if not r["cache_hit"]), "cache_hits": sum(r["calls"] for r in by if r["cache_hit"]),
            "breakdown": by}


CHARS_PER_TOKEN = 2.8     # rough for Russian; the estimate is labelled as such in the UI
OUT_TOKENS = {"detect": 700, "grade": 1800}


@router.get("/{run_id}/estimate")
async def run_estimate(run_id: int, state: State, _: CurrentUser, db: Db) -> dict:
    """Pre-run cost estimate: (prompt + work text) tokens x price, for works that passed the technical filter."""
    run = await get_run(db, run_id)
    cfg = RunConfig.model_validate(run.config or {})
    eff = await appsettings.load(state.sessions, state.settings)
    teams = (await db.execute(select(Team).where(Team.run_id == run_id))).scalars().all()
    eligible = [t for t in teams if t.precheck_status != "FAIL"]
    text_chars = sum((t.chars_with_spaces or 6000) for t in eligible)
    lines, total = [], 0.0
    plan = [("grade", eff.defaults.grade_prompt, eff.defaults.grade_model)]
    if eff.detect_enabled:
        plan.insert(0, ("detect", eff.defaults.detect_prompt, eff.defaults.detect_model))
    for stage, pname, mid in plan:
        try:
            p, m = await appsettings.resolve(db, pname, mid)
        except ValueError:
            continue
        tin = len(eligible) * len(p.text) / CHARS_PER_TOKEN + text_chars / CHARS_PER_TOKEN + len(eligible) * 300
        tout = len(eligible) * OUT_TOKENS[stage]
        cost = tin * m.price_in_per_1m / 1e6 + tout * m.price_out_per_1m / 1e6
        total += cost
        lines.append({"stage": stage, "variant": f"{p.name} v{p.version} · {m.id}", "teams": len(eligible),
                      "tokens_in": int(tin), "tokens_out": int(tout), "cost_rub": round(cost, 2)})
    return {"teams_total": len(teams), "teams_eligible": len(eligible), "lines": lines, "total_rub": round(total, 2),
            "cap_rub": eff.budget_cap_rub, "note": "Approximate: tokens ~ chars/2.8; prices from the model registry; cache hits cost 0."}


@router.get("/{run_id}/teams/{team_id}/export-files")
async def team_export_files(run_id: int, team_id: int, state: State, _: CurrentUser, db: Db) -> list[dict]:
    """Stage 5 files of ONE work (feedback texts, detection, extracted text) in the result bucket."""
    team = await get_team(db, run_id, team_id)
    pref = keys.result_prefix(run_id)
    out = []
    for sub in (f"{pref}{team.rel_dir}/", f"{pref}pipeline_results/{team.rel_dir}/"):
        out += [{"path": k[len(pref):], "bytes": b} for k, b in await state.stores.result.list_meta(sub)]
    return out


@router.get("/{run_id}/teams/{team_id}/llm-calls")
async def team_calls(run_id: int, team_id: int, _: CurrentUser, db: Db) -> list[dict]:
    rows = (await db.execute(select(LlmCall).where(LlmCall.run_id == run_id, LlmCall.team_id == team_id)
                             .order_by(LlmCall.id.desc()).limit(100))).scalars()
    return [{"id": c.id, "stage": c.stage, "variant": c.variant, "model": c.model_id, "prompt": f"{c.prompt_name} v{c.prompt_version}",
             "prompt_tokens": c.prompt_tokens, "completion_tokens": c.completion_tokens, "cost_rub": c.cost_rub,
             "latency_ms": c.latency_ms, "cache_hit": c.cache_hit, "status": c.status, "error": c.error, "at": c.at} for c in rows]


@router.post("/{run_id}/upload")
async def upload(run_id: int, state: State, user: Admin, db: Db, submissions: UploadFile = File(...),
                 worksheet: UploadFile | None = File(None), batch_name: str | None = Form(None)) -> dict:
    """Stage 1 from the browser: creates a NEW batch (zip + optional worksheet CSV) and attaches it to this run.
    The batch name is yours (default YYYYMMDD-HHMM); an existing name is refused."""
    run = await get_run(db, run_id)
    ensure_not_deleting(run)
    from api import batch_ops as ops
    name = ops.check_name(batch_name) if batch_name else await ops.unique_default(state, db)
    row = await ops.store_upload(state, db, user, name, submissions, worksheet)
    await db.flush()
    await ops.use_batch(state, db, run, row.name, user)
    db.add(AuditLog(user_id=user.id, action="upload", details={"run": run_id, "batch": row.name}))
    await db.commit()
    await state.queue.enqueue_job("ingest_run_job", run_id, _job_id=f"ingest:{run_id}:{int(__import__('time').time())}")
    return {"ok": True, "batch": row.name}


class IncomingUse(BaseModel):
    batch: str = Field(min_length=1, max_length=300)


@router.post("/{run_id}/use-incoming")
async def use_incoming(run_id: int, body: IncomingUse, state: State, user: Admin, db: Db) -> dict:
    """Stage 1 from the bucket: attach a valid, unchanged batch (incoming/<batch>/) to this run. Can be called again
    with ANOTHER batch to add late files; a batch itself is never extended."""
    run = await get_run(db, run_id)
    ensure_not_deleting(run)
    from api import batch_ops as ops
    src = await ops.use_batch(state, db, run, body.batch, user)
    db.add(AuditLog(user_id=user.id, action="use_incoming", details={"run": run_id, "batch": body.batch}))
    await db.commit()
    await state.queue.enqueue_job("ingest_run_job", run_id, _job_id=f"ingest:{run_id}:{int(__import__('time').time())}")
    return {"ok": True, "batches": src["batches"]}


@router.post("/{run_id}/export")
async def start_export(run_id: int, state: State, user: Admin, db: Db) -> dict:
    """Stage 5: (re)build the result tree in the result bucket from the stored grades."""
    run = await get_run(db, run_id)
    ensure_not_deleting(run)
    db.add(AuditLog(user_id=user.id, action="export", details={"run": run_id}))
    await db.commit()
    await state.queue.enqueue_job("export_run_job", run_id, _job_id=f"export:{run_id}")
    return {"queued": True}


@router.get("/{run_id}/export")
async def export_info(run_id: int, state: State, _: CurrentUser, db: Db) -> dict:
    run = await get_run(db, run_id)
    pref = keys.result_prefix(run_id)
    files = [{"path": k[len(pref):], "bytes": b} for k, b in await state.stores.result.list_meta(pref)]
    return {"last_export": run.last_export, "bucket": state.stores.result.bucket, "prefix": pref, "files": files}


@router.get("/{run_id}/export/file")
async def export_file(run_id: int, path: str, state: State, _: CurrentUser, db: Db):
    await get_run(db, run_id)
    if ".." in path.split("/"):
        raise HTTPException(400, "Bad path")
    raw = await state.stores.result.get(f"{keys.result_prefix(run_id)}{path}")
    if raw is None:
        raise HTTPException(404, "File not found")
    from urllib.parse import quote
    media = ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if path.endswith(".xlsx") else
             "application/json" if path.endswith(".json") else "text/csv" if path.endswith(".csv") else "text/plain; charset=utf-8")
    return Response(raw, media_type=media, headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(path.rsplit('/', 1)[-1])}"})


@router.get("/{run_id}/export/zip")
async def export_zip(run_id: int, state: State, _: CurrentUser, db: Db, team_id: int | None = None):
    """Download the whole export of a run (or only the files of one work) as a zip. Open to viewers."""
    import io
    import zipfile
    from urllib.parse import quote
    run = await get_run(db, run_id)
    pref = keys.result_prefix(run_id)
    if team_id is not None:
        team = await get_team(db, run_id, team_id)
        wanted = [f"{pref}{team.rel_dir}/", f"{pref}pipeline_results/{team.rel_dir}/"]
        fname = f"{team.name}_results.zip"
    else:
        wanted, fname = [pref], f"run{run_id}_results.zip"
    metas = [(k, b) for pre in wanted for k, b in await state.stores.result.list_meta(pre)]
    if not metas:
        raise HTTPException(404, "Nothing exported yet: build the export first")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for k, _b in metas:
            zf.writestr(k[len(pref):], await state.stores.result.get(k) or b"")
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(fname)}"})


class StageStart(BaseModel):
    only_failed: bool = False


@router.post("/{run_id}/stages/{stage}/start")
async def start_stage(run_id: int, stage: str, body: StageStart, state: State, user: Admin, db: Db) -> dict:
    """Enqueue every team whose item is not 'done' (= resume), or only failed ones."""
    run = await get_run(db, run_id)
    ensure_not_deleting(run)
    if stage not in pipeline.IMPLEMENTED_STAGES:
        raise HTTPException(400, f"Stage {stage} is not available. Available: {list(pipeline.IMPLEMENTED_STAGES)}")
    ctx = pipeline.Ctx(state.sessions, state.stores, state.settings, state.secrets, state.redis)
    if stage == "s3_detect" and not (await appsettings.load(state.sessions, state.settings)).detect_enabled:
        raise HTTPException(409, "AI detection is switched off on the Settings page")
    todo = await pipeline.queue_stage(ctx, run_id, stage, body.only_failed)
    for tid in todo:
        await pipeline.set_item(ctx, run_id, tid, stage, "queued", error=None)
        await state.queue.enqueue_job("stage_item_job", run_id, tid, stage, _job_id=f"{run_id}:{tid}:{stage}")
    db.add(AuditLog(user_id=user.id, action="stage_start", details={"run": run_id, "stage": stage, "n": len(todo)}))
    await db.commit()
    return {"queued": len(todo)}


@router.post("/{run_id}/run-all")
async def run_all(run_id: int, body: StageStart, state: State, user: Admin, db: Db) -> dict:
    """Resume / run ALL stages (2 -> 3 if on -> 4 -> export) for every work in one click.
    Finished units are skipped by their manifests, so this is also the 'resume' button."""
    run = await get_run(db, run_id)
    ensure_not_deleting(run)
    if run.status not in ("ready",):
        raise HTTPException(409, f"Run is '{run.status}': wait until stage 1 is finished (status ready)")
    ctx = pipeline.Ctx(state.sessions, state.stores, state.settings, state.secrets, state.redis)
    todo = await pipeline.queue_stage(ctx, run_id, "s4_grade", body.only_failed)
    for tid in todo:
        await pipeline.set_item(ctx, run_id, tid, "s2_extract", "queued", error=None)
        await state.queue.enqueue_job("team_pipeline_job", run_id, tid, _job_id=f"all:{run_id}:{tid}")
    db.add(AuditLog(user_id=user.id, action="run_all", details={"run": run_id, "n": len(todo)}))
    await db.commit()
    return {"queued": len(todo)}


@router.post("/{run_id}/reingest")
async def reingest(run_id: int, state: State, user: Admin, db: Db) -> dict:
    """Re-read the run's source (UI upload or incoming batch): picks up late files, skips what is already copied."""
    run = await get_run(db, run_id)
    ensure_not_deleting(run)
    if not run.source:
        raise HTTPException(409, "This run has no source yet: upload files or choose a batch first")
    run.status = "uploaded"
    db.add(AuditLog(user_id=user.id, action="reingest", details={"run": run_id}))
    await db.commit()
    await state.queue.enqueue_job("ingest_run_job", run_id, _job_id=f"ingest:{run_id}:{int(__import__('time').time())}")
    return {"ok": True}


class RunDelete(BaseModel):
    confirm: str = ""


def ensure_not_deleting(run: Run) -> None:
    """A run being deleted must not accept new work: its prefixes may already be gone."""
    if run.status == "deleting":
        raise HTTPException(409, "This run is being deleted right now")


@router.post("/{run_id}/delete")
async def delete_run(run_id: int, body: RunDelete, state: State, user: Admin, db: Db) -> dict:
    """Queue a staged deletion of the whole run (sources, work files, exported results, database rows),
    confirmed by typing DELETE (the same pattern as clearing the cache in Settings). The worker reports
    live progress per stage over SSE; when it finishes, the UI returns to the Runs list. The shared
    LLM/OCR cache (_cache/) and the incoming batches are NOT touched, so other runs are unaffected."""
    run = await get_run(db, run_id)
    if body.confirm != "DELETE":
        raise HTTPException(400, "Type DELETE to confirm")
    if run.status == "deleting":
        raise HTTPException(409, "The run is already being deleted")
    busy = run.status == "ingesting" or (await db.execute(
        select(StageItem.id).where(StageItem.run_id == run_id,
                                   StageItem.status.in_(("queued", "running"))).limit(1))).scalar_one_or_none()
    if busy:
        raise HTTPException(409, "The run is being processed right now; wait until it is idle, then delete")
    run.status = "deleting"
    db.add(AuditLog(user_id=user.id, action="run_delete_start", details={"run": run_id, "name": run.name}))
    await db.commit()
    await state.queue.enqueue_job("delete_run_job", run_id, user.id, _job_id=f"delete:{run_id}")   # same id => queued once
    return {"queued": True}


@router.get("/{run_id}/teams")
async def list_teams(run_id: int, state: State, _: CurrentUser, db: Db) -> list[dict]:
    await get_run(db, run_id)
    limit = state.settings.processing.char_limit
    teams = (await db.execute(select(Team).where(Team.run_id == run_id).order_by(Team.name))).scalars().all()
    items: dict[int, dict[str, dict]] = {}
    for it in (await db.execute(select(StageItem).where(StageItem.run_id == run_id))).scalars():
        items.setdefault(it.team_id, {})[it.stage] = {"status": it.status, "error": it.error,
                                                      "cache_hit": it.cache_hit, "attempts": it.attempts}
    return [{"id": t.id, "name": t.name, "slug": t.slug, "filename": t.filename, "is_pdf": t.is_pdf,
             "csv_matched": t.csv_matched, "precheck_status": t.precheck_status, "precheck_code": t.precheck_code,
             "chars_with_spaces": t.chars_with_spaces, "slide_count": t.slide_count,
             "grade_total": t.grade_total, "grade_status": t.grade_status, "ai_probability": t.ai_probability,
             "review_flags": t.review_flags or [],
             "over_char_limit": bool(t.chars_with_spaces and t.chars_with_spaces > limit), "char_limit": limit,
             "stages": items.get(t.id, {})} for t in teams]


async def get_team(db, run_id: int, team_id: int) -> Team:
    team = await db.get(Team, team_id)
    if team is None or team.run_id != run_id:
        raise HTTPException(404, "Team not found")
    return team


@router.get("/{run_id}/teams/{team_id}")
async def team_detail(run_id: int, team_id: int, state: State, _: CurrentUser, db: Db) -> dict:
    team = await get_team(db, run_id, team_id)
    names = [k.rsplit("/", 1)[-1] for k in await state.stores.data.list(keys.work_prefix(run_id, team.slug))]
    items = (await db.execute(select(StageItem).where(StageItem.team_id == team_id))).scalars().all()
    return {"id": team.id, "name": team.name, "filename": team.filename, "is_pdf": team.is_pdf,
            "extra_files": team.extra_files, "csv_matched": team.csv_matched, "artifacts": sorted(names),
            "stages": {i.stage: {"status": i.status, "error": i.error, "attempts": i.attempts,
                                 "cache_hit": i.cache_hit, "duration_ms": i.duration_ms} for i in items}}


@router.get("/{run_id}/teams/{team_id}/artifacts/{name}")
async def artifact(run_id: int, team_id: int, name: str, state: State, _: CurrentUser, db: Db):
    team = await get_team(db, run_id, team_id)
    if not ARTIFACT_RE.match(name):
        raise HTTPException(400, "Bad artifact name")
    raw = await state.stores.data.get(keys.artifact(run_id, team.slug, name))
    if raw is None:
        raise HTTPException(404, "Artifact not found")
    return Response(raw, media_type="application/json" if name.endswith(".json") else "text/plain; charset=utf-8")


@router.get("/{run_id}/teams/{team_id}/file")
async def team_file(run_id: int, team_id: int, state: State, _: CurrentUser, db: Db):
    team = await get_team(db, run_id, team_id)
    raw = await state.stores.data.get(team.file_key)
    if raw is None:
        raise HTTPException(404, "File not found")
    return Response(raw, media_type="application/pdf" if team.is_pdf else "application/octet-stream",
                    headers={"Content-Disposition": "inline", "X-Content-Type-Options": "nosniff"})


def sse_line(msg: dict | None) -> str:
    """One SSE frame from a redis pub/sub message. The payload can arrive as bytes (the client is
    created without decode_responses), and a bare f-string would render it as `data: b'{...}'` —
    which the browser's JSON.parse silently rejects, dropping every live event (progress, run
    status, deletion) — so bytes are decoded here."""
    if not msg:
        return ": keepalive\n\n"
    data = msg["data"]
    if isinstance(data, bytes):
        data = data.decode()
    return f"data: {data}\n\n"


@router.get("/{run_id}/events")
async def run_events(run_id: int, state: State, _: CurrentUser):
    """Server-Sent Events: the browser keeps one HTTP connection open and receives progress pushes."""
    pubsub = state.redis.pubsub()
    await pubsub.subscribe(events.channel(run_id))

    async def stream():
        try:
            yield "retry: 3000\n\n"
            while True:
                yield sse_line(await pubsub.get_message(ignore_subscribe_messages=True, timeout=15))
        finally:
            await pubsub.unsubscribe()
            await pubsub.aclose()

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
