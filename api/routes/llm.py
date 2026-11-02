"""Prompt store, model registry, playground (test one prompt on one team)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from api.deps import Admin, CurrentUser, Db, State
from core import appsettings, keys, registry
from core.db import AuditLog, ModelEntry, PromptVersion, Team
from core.llm import BudgetExceeded, CallTag, Gateway, LLMError
from core.stages import grading
from core.storage import get_json

router = APIRouter(prefix="/api", tags=["llm"])


def prompt_out(p: PromptVersion, with_text: bool = False) -> dict:
    d = {"id": p.id, "name": p.name, "kind": p.kind, "version": p.version, "hash": p.hash, "status": p.status,
         "note": p.note, "chars": len(p.text), "est_tokens": int(len(p.text) / 2.8), "created_at": p.created_at}
    if with_text:
        d["text"] = p.text
    return d


def model_out(m: ModelEntry) -> dict:
    return {c: getattr(m, c) for c in ("id", "provider", "model", "label", "price_in_per_1m", "price_out_per_1m", "price_cached_per_1m",
                                       "max_concurrency", "reasoning_effort", "enabled", "prices_checked")}


@router.get("/prompts")
async def list_prompts(_: Admin, db: Db) -> list[dict]:
    rows = (await db.execute(select(PromptVersion).order_by(PromptVersion.name, PromptVersion.version.desc()))).scalars()
    return [prompt_out(p) for p in rows]


@router.get("/prompts/{prompt_id}")
async def get_prompt(prompt_id: int, _: Admin, db: Db) -> dict:
    p = await db.get(PromptVersion, prompt_id)
    if p is None:
        raise HTTPException(404)
    return prompt_out(p, with_text=True)


class PromptCreate(BaseModel):
    name: str = Field(pattern=r"^[a-z0-9_]{2,40}$")
    text: str = Field(min_length=20)
    note: str = ""


@router.post("/prompts", status_code=201)
async def create_prompt(body: PromptCreate, admin: Admin, db: Db) -> dict:
    """Prompts are immutable once saved: an edit is a NEW version, so runs pinned to old versions stay reproducible."""
    existing = (await db.execute(select(PromptVersion).where(PromptVersion.name == body.name).limit(1))).scalar_one_or_none()
    kind = existing.kind if existing else ("detect" if "detect" in body.name else "grade")
    v = await registry.next_version(db, body.name)
    p = PromptVersion(name=body.name, kind=kind, version=v, text=body.text, hash=registry.text_hash(body.text),
                      status="draft", note=body.note, created_by=admin.id)
    db.add(p)
    db.add(AuditLog(user_id=admin.id, action="prompt_create", details={"name": body.name, "version": v}))
    await db.commit()
    return prompt_out(p)


@router.post("/prompts/{prompt_id}/publish")
async def publish_prompt(prompt_id: int, admin: Admin, db: Db) -> dict:
    p = await db.get(PromptVersion, prompt_id)
    if p is None:
        raise HTTPException(404)
    p.status = "published"
    db.add(AuditLog(user_id=admin.id, action="prompt_publish", details={"id": p.id, "name": p.name, "version": p.version}))
    await db.commit()
    return prompt_out(p)


class PlaygroundBody(BaseModel):
    run_id: int
    team_id: int
    prompt_id: int
    model_id: str
    fresh: bool = False          # skip the answer cache: always a new paid call


@router.get("/playground/options")
async def playground_options(state: State, _: CurrentUser, db: Db) -> dict:
    """What the Playground may choose from. Open to viewers: names and sizes only, never prompt texts or prices."""
    eff = await appsettings.load(state.sessions, state.settings)
    prompts = (await db.execute(select(PromptVersion).order_by(PromptVersion.name, PromptVersion.version.desc()))).scalars().all()
    models = (await db.execute(select(ModelEntry).where(ModelEntry.enabled).order_by(ModelEntry.id))).scalars().all()
    return {"prompts": [{"id": p.id, "name": p.name, "version": p.version, "status": p.status, "est_tokens": int(len(p.text) / 2.8)} for p in prompts],
            "models": [{"id": m.id, "label": m.label or m.id} for m in models],
            "defaults": {"grade_prompt": eff.defaults.grade_prompt, "grade_model": eff.defaults.grade_model}}


@router.post("/playground/run")
async def playground_run(body: PlaygroundBody, state: State, _: CurrentUser, db: Db) -> dict:
    """Run ONE prompt on ONE work (stage 2 must be done). Nothing is saved as a result; the call is billed to the run
    and refused once the run's budget cap is reached. Open to viewers (so is every other read of results)."""
    p, m, team = await db.get(PromptVersion, body.prompt_id), await db.get(ModelEntry, body.model_id), await db.get(Team, body.team_id)
    if p is None or m is None or not m.enabled or team is None or team.run_id != body.run_id:
        raise HTTPException(404, "Prompt, model or work not found")
    pages = await get_json(state.stores.data, keys.artifact(team.run_id, team.slug, "s2_pages.json"))
    metrics = await get_json(state.stores.data, keys.artifact(team.run_id, team.slug, "s2_metrics.json"))
    pc = await get_json(state.stores.data, keys.artifact(team.run_id, team.slug, "s2_precheck.json"))
    if pages is None:
        raise HTTPException(409, "Run stage 2 for this work first")
    eff = await appsettings.load(state.sessions, state.settings)
    gw = Gateway(state.settings, state.secrets, state.sessions, state.stores.data)
    user = grading.build_user_message(team.name, pages, metrics, pc)
    try:
        res = await gw.complete(m, p.text, user, CallTag(body.run_id, team.id, "playground", "", p.name, p.version),
                                cap_rub=eff.budget_cap_rub, use_cache=not body.fresh)
    except BudgetExceeded as exc:
        raise HTTPException(402, str(exc)) from exc
    except LLMError as exc:
        raise HTTPException(502, str(exc)) from exc
    parsed, error = None, None
    try:
        raw = grading.parse_json_object(res.text)
        parsed = (grading.finalize_grade(raw, team.name).model_dump() if p.kind == "grade" else raw)
    except grading.GradeError as exc:
        error = str(exc)
    return {"raw": res.text, "parsed": parsed, "error": error, "prompt_tokens": res.prompt_tokens,
            "completion_tokens": res.completion_tokens, "cost_rub": res.cost_rub, "latency_ms": res.latency_ms,
            "cache_hit": res.cache_hit, "input_full": user, "prompt": f"{p.name} v{p.version}", "model": m.label or m.id, "team": team.name}


@router.get("/models")
async def list_models(_: Admin, db: Db) -> list[dict]:
    return [model_out(m) for m in (await db.execute(select(ModelEntry).order_by(ModelEntry.id))).scalars()]


class ModelPatch(BaseModel):
    price_in_per_1m: float | None = Field(None, ge=0)
    price_out_per_1m: float | None = Field(None, ge=0)
    max_concurrency: int | None = Field(None, ge=1, le=32)
    enabled: bool | None = None
    reasoning_effort: str | None = Field(None, pattern=r"^(none|low|medium|high|)$")
    price_cached_per_1m: float | None = Field(None, ge=0)
    prices_checked: str | None = None


@router.patch("/models/{model_id}")
async def patch_model(model_id: str, body: ModelPatch, admin: Admin, db: Db) -> dict:
    m = await db.get(ModelEntry, model_id)
    if m is None:
        raise HTTPException(404)
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(m, k, v)
    db.add(AuditLog(user_id=admin.id, action="model_patch", details={"id": model_id, **body.model_dump(exclude_none=True)}))
    await db.commit()
    return model_out(m)
