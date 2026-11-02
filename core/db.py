"""Postgres schema (SQLAlchemy 2.0 async). State and progress live here; artifacts live in S3.

Postgres can be rebuilt from S3 manifests (core.reindex, planned), so S3 stays the
source of truth for content. M1 creates tables with ``create_all``; Alembic comes
with the first schema change after the pilot.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from core.config import Secrets, Settings


def now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    yandex_uid: Mapped[str | None] = mapped_column(String(64), unique=True)
    login: Mapped[str] = mapped_column(String(128), default="")
    email: Mapped[str] = mapped_column(String(256), unique=True)  # stored lower-case
    name: Mapped[str] = mapped_column(String(256), default="")
    role: Mapped[str] = mapped_column(String(16), default="viewer")  # admin | viewer
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(24), default="created")  # created|ingesting|ready|...
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    note: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[dict] = mapped_column(JSON, default=dict)        # {"kind": "upload"|"incoming", "prefix": ...}
    last_export: Mapped[dict | None] = mapped_column(JSON)          # summary of the latest stage-5 export


class Team(Base):
    __tablename__ = "teams"
    __table_args__ = (UniqueConstraint("run_id", "slug"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(300))
    slug: Mapped[str] = mapped_column(String(120))
    submission_id: Mapped[str] = mapped_column(String(32), default="")
    filename: Mapped[str] = mapped_column(String(400), default="")
    rel_dir: Mapped[str] = mapped_column(String(800), default="")    # "<assignment>/<Team>_<id>_assignsubmission_file"
    file_key: Mapped[str] = mapped_column(String(600), default="")
    file_sha256: Mapped[str] = mapped_column(String(64), default="")
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    is_pdf: Mapped[bool] = mapped_column(Boolean, default=True)
    extra_files: Mapped[list] = mapped_column(JSON, default=list)
    csv_matched: Mapped[bool | None] = mapped_column(Boolean)
    # denormalised result of stage 2/4 for fast tables; the artifacts in S3 are authoritative
    precheck_status: Mapped[str | None] = mapped_column(String(8))
    precheck_code: Mapped[str | None] = mapped_column(String(32))
    chars_with_spaces: Mapped[int | None] = mapped_column(Integer)
    slide_count: Mapped[int | None] = mapped_column(Integer)
    grade_total: Mapped[int | None] = mapped_column(Integer)        # primary variant, stage 4
    grade_status: Mapped[str | None] = mapped_column(String(16))
    ai_probability: Mapped[float | None] = mapped_column()          # primary variant, stage 3
    # reviewer hints about manipulation: "s2:HIDDEN_TEXT", "s2:INJECTION_SUSPECTED", "s3:MANIPULATION" (never change scores)
    review_flags: Mapped[list] = mapped_column(JSON, default=list)


class StageItem(Base):
    __tablename__ = "stage_items"
    __table_args__ = (UniqueConstraint("run_id", "team_id", "stage"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), index=True)
    stage: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(12), default="pending")  # pending|queued|running|done|failed|skipped
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)  # True = resumed from existing manifest
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class AppSetting(Base):
    """Admin-editable overrides of settings.json values (defaults for new runs, export texts)."""
    __tablename__ = "app_settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict | list | str | None] = mapped_column(JSON)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class ApiKey(Base):
    """Key for outside services. Only a SHA-256 hash is stored; the key itself is shown once at creation."""
    __tablename__ = "api_keys"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    prefix: Mapped[str] = mapped_column(String(16))                  # first characters, to recognise the key in lists
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    role: Mapped[str] = mapped_column(String(16), default="viewer")  # viewer = read-only, admin = everything except key management
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Batch(Base):
    """Registry of upload batches (incoming/<name>/). The files themselves live in the bucket."""
    __tablename__ = "batches"
    name: Mapped[str] = mapped_column(String(80), primary_key=True)
    origin: Mapped[str] = mapped_column(String(12), default="cli")           # ui | cli | outside (found in the bucket, never reserved)
    status: Mapped[str] = mapped_column(String(12), default="reserved")      # reserved | uploaded | used
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    sealed_hash: Mapped[str | None] = mapped_column(String(32))               # listing hash at the moment a run first used it
    sealed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(64))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ModelEntry(Base):
    """LLM registry. Prices are rubles per 1M tokens; edited by admins, never hard-coded in logic."""
    __tablename__ = "llm_models"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)        # e.g. "yandex-deepseek-v4-flash"
    provider: Mapped[str] = mapped_column(String(16))                    # yandex | cloudru
    model: Mapped[str] = mapped_column(String(128))                      # provider-side model name
    label: Mapped[str] = mapped_column(String(128), default="")
    price_in_per_1m: Mapped[float] = mapped_column(default=0.0)
    price_out_per_1m: Mapped[float] = mapped_column(default=0.0)
    price_cached_per_1m: Mapped[float] = mapped_column(default=0.0)   # 0 = same as price_in
    max_concurrency: Mapped[int] = mapped_column(Integer, default=4)
    reasoning_effort: Mapped[str] = mapped_column(String(16), default="")  # "none" for DeepSeek on Yandex
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    prices_checked: Mapped[str] = mapped_column(String(32), default="")


class PromptVersion(Base):
    __tablename__ = "prompt_versions"
    __table_args__ = (UniqueConstraint("name", "version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), index=True)            # grade_long | grade_short | ai_detect
    kind: Mapped[str] = mapped_column(String(16), default="grade")       # grade | detect
    version: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    hash: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(12), default="draft")     # draft | published
    note: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class LlmCall(Base):
    __tablename__ = "llm_calls"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    team_id: Mapped[int | None] = mapped_column(Integer)
    stage: Mapped[str] = mapped_column(String(40))
    variant: Mapped[str] = mapped_column(String(80), default="")
    model_id: Mapped[str] = mapped_column(String(64))
    prompt_name: Mapped[str] = mapped_column(String(64), default="")
    prompt_version: Mapped[int] = mapped_column(Integer, default=0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0)   # prompt tokens billed at the cached price
    cost_rub: Mapped[float] = mapped_column(default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(12), default="ok")         # ok | error
    error: Mapped[str | None] = mapped_column(Text)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


def db_url(settings: Settings, secrets: Secrets) -> URL | str:
    if override := os.environ.get("FEDLAB_DB_URL"):  # tests / local experiments
        return override
    d = settings.database
    return URL.create("postgresql+asyncpg", username=d.user, password=secrets.require("app", "postgres_password"),
                      host=d.host, port=d.port, database=d.name)


def make_session_factory(url: URL | str) -> tuple[async_sessionmaker[AsyncSession], object]:
    engine = create_async_engine(url, pool_pre_ping=True)
    return async_sessionmaker(engine, expire_on_commit=False), engine


async def create_all(engine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def ensure_run_id_start(sessions, start: int) -> None:
    """Postgres only: if no run exists yet, make the id sequence start at ``start``."""
    if start <= 1:
        return
    from sqlalchemy import text
    async with sessions() as s:
        if s.bind.dialect.name != "postgresql":
            return
        if (await s.execute(text("select count(*) from runs"))).scalar_one() == 0:
            await s.execute(text(f"select setval(pg_get_serial_sequence('runs','id'), {int(start)}, false)"))
            await s.commit()
