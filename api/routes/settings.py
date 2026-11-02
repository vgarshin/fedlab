"""Admin-editable defaults (models/prompts for NEW runs) and export wording/format."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from api.deps import Admin, CurrentUser, Db, State
from core import appsettings
from core.db import AuditLog, ModelEntry, PromptVersion

router = APIRouter(prefix="/api/settings", tags=["settings"])


class DefaultsPatch(BaseModel):
    grade_model: str | None = None
    detect_model: str | None = None
    grade_prompt: str | None = None
    detect_prompt: str | None = None


class ExportPatch(BaseModel):
    csv_mode: Literal["auto", "comma", "tab", "both"] | None = None
    score_line: str | None = Field(None, min_length=3, max_length=200)
    feedback_header: str | None = Field(None, min_length=1, max_length=200)


class StagesPatch(BaseModel):
    detect_enabled: bool | None = None


class BudgetPatch(BaseModel):
    run_cap_rub: float | None = Field(None, gt=0, le=1_000_000)


class OcrPatch(BaseModel):
    max_pages: int | None = Field(None, ge=1, le=500)


class SettingsPatch(BaseModel):
    ocr: OcrPatch | None = None
    budget: BudgetPatch | None = None
    stages: StagesPatch | None = None
    defaults: DefaultsPatch | None = None
    export: ExportPatch | None = None


@router.get("")
async def get_settings_view(state: State, _: Admin, db: Db) -> dict:
    eff = await appsettings.load(state.sessions, state.settings)
    names = sorted({p.name for p in (await db.execute(select(PromptVersion).where(PromptVersion.status == "published"))).scalars()})
    return {"defaults": eff.defaults.model_dump(), "export": eff.export.model_dump(), "stages": {"detect_enabled": eff.detect_enabled}, "budget": {"run_cap_rub": eff.budget_cap_rub},
            "ocr": {"max_pages": eff.ocr_max_pages, "price_per_page_rub": state.settings.ocr.price_per_page_rub},
            "processing": {"max_upload_mb": state.settings.processing.max_upload_mb},
            "prompt_names": names}


@router.put("")
async def put_settings(body: SettingsPatch, state: State, admin: Admin, db: Db) -> dict:
    if body.defaults:
        vals = body.defaults.model_dump(exclude_none=True)
        for k in ("grade_model", "detect_model"):
            if k in vals:
                m = await db.get(ModelEntry, vals[k])
                if m is None or not m.enabled:
                    raise HTTPException(400, f"{vals[k]} is not an enabled model")
        for k in ("grade_prompt", "detect_prompt"):
            if k in vals and (await db.execute(select(PromptVersion).where(PromptVersion.name == vals[k],
                                                                           PromptVersion.status == "published").limit(1))).scalar_one_or_none() is None:
                raise HTTPException(400, f"no published prompt named {vals[k]}")
        await appsettings.save(db, "defaults", vals, admin.id)
    if body.ocr:
        await appsettings.save(db, "ocr", body.ocr.model_dump(exclude_none=True), admin.id)
    if body.budget:
        await appsettings.save(db, "budget", body.budget.model_dump(exclude_none=True), admin.id)
    if body.stages:
        await appsettings.save(db, "stages", body.stages.model_dump(exclude_none=True), admin.id)
    if body.export:
        await appsettings.save(db, "export", body.export.model_dump(exclude_none=True), admin.id)
    db.add(AuditLog(user_id=admin.id, action="settings", details=body.model_dump(exclude_none=True)))
    await db.commit()
    return await get_settings_view(state, admin, db)


# ---------- cache (LLM answers and OCR pages stored in the data bucket under _cache/)
CACHE_KINDS = {"llm": "_cache/llm/", "ocr": "_cache/ocr/"}


@router.get("/cache")
async def cache_info(state: State, _: Admin) -> dict:
    out = {}
    for kind, prefix in CACHE_KINDS.items():
        metas = await state.stores.data.list_meta(prefix)
        out[kind] = {"objects": len(metas), "bytes": sum(b for _, b in metas)}
    return out


class CacheClear(BaseModel):
    llm: bool = False
    ocr: bool = False
    confirm: str = ""


@router.post("/cache/clear")
async def cache_clear(body: CacheClear, state: State, admin: Admin, db: Db) -> dict:
    """Delete cached LLM answers and/or OCR pages. Stored results of runs are NOT touched."""
    if body.confirm != "CLEAR":
        raise HTTPException(400, "Type CLEAR to confirm")
    if not (body.llm or body.ocr):
        raise HTTPException(400, "Choose what to clear")
    removed = {}
    for kind in ("llm", "ocr"):
        if getattr(body, kind):
            keys_ = await state.stores.data.list(CACHE_KINDS[kind])
            for k in keys_:
                await state.stores.data.delete(k)
            removed[kind] = len(keys_)
    db.add(AuditLog(user_id=admin.id, action="cache_clear", details=removed))
    await db.commit()
    return {"removed": removed}
