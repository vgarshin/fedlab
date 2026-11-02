"""Seed the model registry and the prompt store (idempotent; admins edit them in the UI afterwards)."""
from __future__ import annotations

import hashlib
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.config import Settings
from core.db import ModelEntry, PromptVersion

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
# file stem -> (prompt name, kind). Files are named <name>_v<N>.md; they seed version N once.
SEED_KIND = {"grade_long": "grade", "grade_short": "grade", "ai_detect": "detect"}


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


async def seed(sessions: async_sessionmaker[AsyncSession], settings: Settings, prompts_dir: Path = PROMPTS_DIR) -> None:
    async with sessions() as s:
        have = set((await s.execute(select(ModelEntry.id))).scalars())
        for m in settings.models:
            if m["id"] not in have:
                s.add(ModelEntry(**m))
        for path in sorted(prompts_dir.glob("*_v*.md")):
            name, _, ver = path.stem.rpartition("_v")
            if name not in SEED_KIND or not ver.isdigit():
                continue
            exists = (await s.execute(select(PromptVersion).where(
                PromptVersion.name == name, PromptVersion.version == int(ver)))).scalar_one_or_none()
            if exists is None:
                text = path.read_text(encoding="utf-8")
                s.add(PromptVersion(name=name, kind=SEED_KIND[name], version=int(ver), text=text,
                                    hash=text_hash(text), status="published", note="seeded from file"))
        await s.commit()


async def next_version(s: AsyncSession, name: str) -> int:
    return ((await s.execute(select(func.max(PromptVersion.version)).where(PromptVersion.name == name))).scalar() or 0) + 1
