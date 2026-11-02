"""Orchestration glue between DB state, stages and events. No web/queue imports."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core import events, keys
from core.config import Secrets, Settings
import asyncio

from core.db import LlmCall, ModelEntry, PromptVersion, Run, StageItem, Team, now
from core.llm import Gateway
from core import appsettings
from core.stages import export as s5, grading
from core.ocr import YandexOCR
from core.schemas import RunConfig
from core.stages import extract as s2, ingest as s1
from core.stores import Stores

IMPLEMENTED_STAGES = ("s2_extract", "s3_detect", "s4_grade")   # per-team stages available in this milestone


@dataclass
class Ctx:
    sessions: async_sessionmaker[AsyncSession]
    stores: Stores
    settings: Settings
    secrets: Secrets
    redis: Any = None
    _ocr: YandexOCR | None = None
    _gw: Gateway | None = None

    def gateway(self) -> Gateway:
        if self._gw is None:
            self._gw = Gateway(self.settings, self.secrets, self.sessions, self.stores.data)
        return self._gw

    def ocr(self) -> YandexOCR:
        """Created lazily: runs without OCR never need the OCR key."""
        if self._ocr is None:
            self._ocr = YandexOCR(self.settings.ocr, self.settings.yandex.folder_id,
                                  self.secrets.require("yandex_ocr", "api_key"), self.stores.data)
        return self._ocr


async def set_item(ctx: Ctx, run_id: int, team_id: int, stage: str, status: str, **fields) -> None:
    async with ctx.sessions() as s:
        item = (await s.execute(select(StageItem).where(
            StageItem.run_id == run_id, StageItem.team_id == team_id, StageItem.stage == stage))).scalar_one_or_none()
        if item is None:
            item = StageItem(run_id=run_id, team_id=team_id, stage=stage)
            s.add(item)
        item.status = status
        for k, v in fields.items():
            setattr(item, k, v)
        await s.commit()
    await events.publish(ctx.redis, run_id, type="item", team_id=team_id, stage=stage, status=status)


async def queue_stage(ctx: Ctx, run_id: int, stage: str, only_failed: bool = False, skip_done: bool = False) -> list[int]:
    """Team ids to enqueue for a stage. By default ALL teams: the stage handler checks S3 manifests and returns
    immediately for finished units, so this is cheap and also picks up config changes (e.g. a newly added
    grading variant) that a 'done' flag in the database cannot know about. ``skip_done`` limits to unfinished."""
    async with ctx.sessions() as s:
        teams = (await s.execute(select(Team.id).where(Team.run_id == run_id).order_by(Team.id))).scalars().all()
        items = {i.team_id: i for i in (await s.execute(
            select(StageItem).where(StageItem.run_id == run_id, StageItem.stage == stage))).scalars()}
    todo = []
    for tid in teams:
        it = items.get(tid)
        if skip_done and it and it.status == "done":
            continue
        if only_failed and not (it and it.status == "failed"):
            continue
        todo.append(tid)
    return todo


async def run_ingest(ctx: Ctx, run_id: int) -> dict:
    async with ctx.sessions() as s:
        run = await s.get(Run, run_id)
        run.status = "ingesting"
        source = dict(run.source or {"kind": "upload"})
        await s.commit()
    await events.publish(ctx.redis, run_id, type="run", status="ingesting")
    t0 = time.perf_counter()
    prog = s1.IngestProgress()

    async def ticker() -> None:
        """Publish progress every 2 s while ingest runs (best-effort; workers only)."""
        while True:
            await events.publish(ctx.redis, run_id, type="ingest", **prog.snapshot())
            await asyncio.sleep(2)

    tick_task = asyncio.create_task(ticker()) if ctx.redis is not None else None
    try:
        teams, report = await s1.ingest(ctx.stores.data, run_id, source, progress=prog)
    finally:
        if tick_task:
            tick_task.cancel()
    done_event = prog.snapshot() | {"percent": 100}
    await events.publish(ctx.redis, run_id, type="ingest", **done_event)
    match = report.pop("_match", {})
    async with ctx.sessions() as s:
        existing = {t.slug: t for t in (await s.execute(select(Team).where(Team.run_id == run_id))).scalars()}
        for t in teams:
            row = existing.get(t.slug) or Team(run_id=run_id, slug=t.slug)
            row.name, row.submission_id, row.filename, row.rel_dir = t.name, t.submission_id, t.filename, t.rel_dir
            row.file_key, row.file_sha256, row.file_size = t.file_key, t.file_sha256, t.file_size
            row.is_pdf, row.extra_files = t.is_pdf, t.extra_files
            row.csv_matched = (match.get(t.name, 0) > 0) if match else None
            s.add(row)
        await s.flush()
        for row in (await s.execute(select(Team).where(Team.run_id == run_id))).scalars():
            it = (await s.execute(select(StageItem).where(
                StageItem.run_id == run_id, StageItem.team_id == row.id, StageItem.stage == s1.STAGE))).scalar_one_or_none()
            if it is None:
                s.add(StageItem(run_id=run_id, team_id=row.id, stage=s1.STAGE, status="done",
                                finished_at=now(), cache_hit=False))
            else:
                it.status = "done"
        run = await s.get(Run, run_id)
        run.status = "ready"
        run.source = {**source, "worksheet_key": report.get("worksheet_key")}
        run.note = f"{len(teams)} teams; unmatched in worksheet: {report.get('unmatched_teams', 'n/a')}"
        await s.commit()
    await events.publish(ctx.redis, run_id, type="run", status="ready")
    report["seconds"] = round(time.perf_counter() - t0, 2)
    return report


async def run_team_stage(ctx: Ctx, run_id: int, team_id: int, stage: str) -> dict:
    """Execute one (team, stage) unit. Raises on failure; caller decides about retries."""
    if stage not in IMPLEMENTED_STAGES:
        raise ValueError(f"stage {stage} is not implemented yet")
    async with ctx.sessions() as s:
        run, team = await s.get(Run, run_id), await s.get(Team, team_id)
        cfg = RunConfig.model_validate(run.config or {})
        item = (await s.execute(select(StageItem).where(
            StageItem.run_id == run_id, StageItem.team_id == team_id, StageItem.stage == stage))).scalar_one_or_none()
        attempts = (item.attempts if item else 0) + 1
    await set_item(ctx, run_id, team_id, stage, "running", attempts=attempts, started_at=now(), error=None)
    t0 = time.perf_counter()
    if stage == "s2_extract":
        result = await _do_extract(ctx, run_id, team, cfg)
    else:
        result = await _do_llm_stage(ctx, run_id, team, cfg, stage)
    await set_item(ctx, run_id, team_id, stage, "done", cache_hit=result["resumed"], finished_at=now(),
                   duration_ms=int((time.perf_counter() - t0) * 1000))
    return result


async def _spent(ctx: Ctx, run_id: int) -> float:
    async with ctx.sessions() as s:
        return float((await s.execute(select(func.coalesce(func.sum(LlmCall.cost_rub), 0.0)).where(LlmCall.run_id == run_id))).scalar_one())


async def _do_extract(ctx: Ctx, run_id: int, team: Team, cfg: RunConfig) -> dict:
    eff = await appsettings.load(ctx.sessions, ctx.settings)

    async def can_spend(rub: float) -> bool:          # OCR is paid per page: respect the run's budget cap
        return await _spent(ctx, run_id) + rub <= eff.budget_cap_rub

    result = await s2.run_extract(
        data=ctx.stores.data, run_id=run_id, slug=team.slug, team_name=team.name, file_key=team.file_key,
        file_sha256=team.file_sha256, is_pdf=team.is_pdf, run_cfg=cfg, proc=ctx.settings.processing,
        ocr_cfg=ctx.settings.ocr, ocr=ctx.ocr() if cfg.ocr_enabled else None, ocr_max_pages=eff.ocr_max_pages,
        can_spend=can_spend)
    paid = result.get("ocr_pages_paid", 0)
    if paid:   # OCR is billed per page; recorded like an LLM call so "spent" and the budget cap include it
        async with ctx.sessions() as s:
            s.add(LlmCall(run_id=run_id, team_id=team.id, stage="s2_extract", variant="", model_id="yandex-ocr",
                          prompt_name="ocr", prompt_version=0, cost_rub=round(paid * ctx.settings.ocr.price_per_page_rub, 6),
                          cache_hit=False, status="ok"))
            await s.commit()
    async with ctx.sessions() as s:
        t = await s.get(Team, team.id)
        if not result["resumed"]:
            t.precheck_status, t.precheck_code = result["precheck"]["status"], result["precheck"]["code"]
            t.chars_with_spaces, t.slide_count = result["chars_with_spaces"], result["slide_count"]
            _set_flags(t, "s2", s2_flags(result["precheck"].get("warnings", [])))
        else:  # refresh the denormalised columns from the artifacts
            import json
            pc = json.loads(await ctx.stores.data.get(keys.artifact(run_id, team.slug, "s2_precheck.json")))
            m = json.loads(await ctx.stores.data.get(keys.artifact(run_id, team.slug, "s2_metrics.json")))
            t.precheck_status, t.precheck_code = pc["status"], pc["code"]
            t.chars_with_spaces, t.slide_count = m["chars_with_spaces"], m["slide_count"]
            _set_flags(t, "s2", s2_flags(pc.get("warnings", [])))
        await s.commit()
    return result


def s2_flags(warnings: list[str]) -> list[str]:
    return [w.split(":", 1)[0] for w in warnings
            if w.startswith(("HIDDEN_TEXT", "INJECTION_SUSPECTED", "PARTIALLY_READABLE"))]


def _set_flags(team: Team, stage: str, names: list[str]) -> None:
    """Replace this stage's flags, keep the other stage's (the column is a denormalised copy for the works table)."""
    keep = [f for f in (team.review_flags or []) if not f.startswith(stage + ":")]
    team.review_flags = keep + [f"{stage}:{n}" for n in names]


async def _do_llm_stage(ctx: Ctx, run_id: int, team: Team, cfg: RunConfig, stage: str) -> dict:
    """Stages 3 and 4 use the DEFAULT model and prompt from the Settings page (latest published prompt version).
    Changing the setting changes the manifest's config hash, so the next Run recomputes only what changed."""
    eff = await appsettings.load(ctx.sessions, ctx.settings)
    if stage == "s3_detect":
        if not eff.detect_enabled:
            raise ValueError("AI detection is switched off on the Settings page")
        pname, mid, fn = eff.defaults.detect_prompt, eff.defaults.detect_model, grading.run_detect
    else:
        pname, mid, fn = eff.defaults.grade_prompt, eff.defaults.grade_model, grading.run_grade
    async with ctx.sessions() as s:
        p, m = await appsettings.resolve(s, pname, mid)
    result = await fn(data=ctx.stores.data, gw=ctx.gateway(), run_id=run_id, team_id=team.id, slug=team.slug,
                      team_name=team.name, prompt_name=p.name, prompt_version=p.version, prompt_text=p.text,
                      prompt_hash=p.hash, model=m, cap_rub=eff.budget_cap_rub)
    async with ctx.sessions() as s:
        t = await s.get(Team, team.id)
        data = await _read_primary(ctx, run_id, team.slug, f"{stage}.json")
        if stage == "s4_grade":
            t.grade_total, t.grade_status = data["result"]["total_score_100"], data["result"]["status"]
        else:
            t.ai_probability = data.get("result", {}).get("ai_probability")
            _set_flags(t, "s3", ["MANIPULATION"] if (data.get("result", {}).get("manipulation_attempt") or {}).get("found") else [])
        await s.commit()
    return result


async def _read_primary(ctx: Ctx, run_id: int, slug: str, name: str) -> dict:
    import json
    return json.loads(await ctx.stores.data.get(keys.artifact(run_id, slug, name)))


async def fail_item(ctx: Ctx, run_id: int, team_id: int, stage: str, error: str) -> None:
    await set_item(ctx, run_id, team_id, stage, "failed", error=error[:2000], finished_at=now())


async def fail_run(ctx: Ctx, run_id: int, error: str) -> None:
    async with ctx.sessions() as s:
        run = await s.get(Run, run_id)
        run.status, run.note = "failed", error[:1000]
        await s.commit()
    await events.publish(ctx.redis, run_id, type="run", status="failed")


async def run_export_job(ctx: Ctx, run_id: int) -> dict:
    """Stage 5 is run-level: rebuilds the whole result tree from stored artifacts (re-runnable any time)."""
    from pathlib import Path

    from sqlalchemy import func as sqlfunc

    from core.db import LlmCall
    async with ctx.sessions() as s:
        run = await s.get(Run, run_id)
        cfg = RunConfig.model_validate(run.config or {})
        teams = [s5.TeamRow(t.name, t.slug, t.rel_dir, t.filename, t.submission_id)
                 for t in (await s.execute(select(Team).where(Team.run_id == run_id))).scalars()]
        spent = float((await s.execute(select(sqlfunc.coalesce(sqlfunc.sum(LlmCall.cost_rub), 0.0))
                                       .where(LlmCall.run_id == run_id))).scalar_one())
        worksheet_key = (run.source or {}).get("worksheet_key")
        name = run.name
    eff = await appsettings.load(ctx.sessions, ctx.settings)
    readme_path = Path(__file__).resolve().parent.parent / "docs" / "RESULTS.md"
    summary = await s5.run_export(data=ctx.stores.data, result=ctx.stores.result, run_id=run_id, run_name=name, cfg=cfg,
                                  teams=teams, worksheet_key=worksheet_key, export_cfg=eff.export,
                                  cost={"spent_rub": round(spent, 4)}, detect_enabled=eff.detect_enabled,
                                  readme=readme_path.read_text(encoding="utf-8") if readme_path.exists() else "")
    async with ctx.sessions() as s:
        run = await s.get(Run, run_id)
        run.last_export = summary
        await s.commit()
    await events.publish(ctx.redis, run_id, type="export", teams=summary["teams_exported"])
    return summary


async def run_team_pipeline(ctx: Ctx, run_id: int, team_id: int) -> dict:
    """'Run all' for one work: stage 2 -> 3 (if switched on) -> 4. Finished units return at once (manifests).
    A failed AI detection never blocks grading (it is informational)."""
    eff = await appsettings.load(ctx.sessions, ctx.settings)
    out: dict = {}
    for stage in ("s2_extract", "s3_detect", "s4_grade"):
        if stage == "s3_detect" and not eff.detect_enabled:
            continue
        await set_item(ctx, run_id, team_id, stage, "queued", error=None)
        try:
            out[stage] = await run_team_stage(ctx, run_id, team_id, stage)
        except Exception as exc:
            await fail_item(ctx, run_id, team_id, stage, f"{type(exc).__name__}: {exc}")
            out[stage] = {"failed": True}
            if stage != "s3_detect":
                if stage == "s2_extract":
                    await fail_item(ctx, run_id, team_id, "s4_grade", "not graded: stage 2 failed")
                break
    return out


async def all_graded(ctx: Ctx, run_id: int) -> bool:
    """True when every work has a finished (done/failed) stage 4 item - time to export."""
    async with ctx.sessions() as s:
        total = (await s.execute(select(func.count()).select_from(Team).where(Team.run_id == run_id))).scalar_one()
        fin = (await s.execute(select(func.count()).select_from(StageItem).where(
            StageItem.run_id == run_id, StageItem.stage == "s4_grade", StageItem.status.in_(("done", "failed"))))).scalar_one()
    return total > 0 and fin >= total
