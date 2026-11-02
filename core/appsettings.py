"""Effective settings = settings.json defaults overridden by admin edits stored in the DB (Settings page)."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.config import DefaultsCfg, ExportCfg, Settings
from core.db import AppSetting, ModelEntry, PromptVersion

DEFAULT_KEYS = ("grade_model", "detect_model", "grade_prompt", "detect_prompt")
EXPORT_KEYS = ("csv_mode", "score_line", "feedback_header")


@dataclass
class Effective:
    defaults: DefaultsCfg
    export: ExportCfg
    detect_enabled: bool
    ocr_max_pages: int         # max slides OCR'd per work (slides without text); more => OCR skipped for that work
    budget_cap_rub: float      # hard cap on a run's total spend (LLM + OCR), checked before each paid LLM call


async def load(sessions: async_sessionmaker[AsyncSession], settings: Settings) -> Effective:
    async with sessions() as s:
        rows = {r.key: r.value for r in (await s.execute(select(AppSetting))).scalars()}
    d = settings.defaults.model_dump() | {k: rows[f"defaults.{k}"] for k in DEFAULT_KEYS if f"defaults.{k}" in rows}
    e = settings.export.model_dump() | {k: rows[f"export.{k}"] for k in EXPORT_KEYS if f"export.{k}" in rows}
    return Effective(DefaultsCfg(**d), ExportCfg(**e), bool(rows.get("stages.detect_enabled", settings.stages.detect_enabled)),
                     int(rows.get("ocr.max_pages", settings.processing.ocr_max_pages)),
                     float(rows.get("budget.run_cap_rub", settings.budget.get("run_cap_rub", 1000))))


async def save(s: AsyncSession, prefix: str, values: dict, user_id: int | None) -> None:
    for k, v in values.items():
        row = await s.get(AppSetting, f"{prefix}.{k}")
        if row is None:
            s.add(AppSetting(key=f"{prefix}.{k}", value=v, updated_by=user_id))
        else:
            row.value, row.updated_by = v, user_id


async def resolve(s: AsyncSession, name: str, model_id: str) -> tuple[PromptVersion, ModelEntry]:
    """Latest PUBLISHED version of a prompt name + an enabled model. Raises ValueError with a fix hint."""
    pv = (await s.execute(select(PromptVersion).where(PromptVersion.name == name, PromptVersion.status == "published")
                          .order_by(PromptVersion.version.desc()))).scalars().first()
    if pv is None:
        raise ValueError(f"No published prompt named '{name}'. Fix on the Settings page.")
    m = await s.get(ModelEntry, model_id)
    if m is None or not m.enabled:
        raise ValueError(f"Model '{model_id}' is missing or disabled. Fix on the Models/Settings page.")
    return pv, m
