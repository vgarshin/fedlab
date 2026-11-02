"""Run `alembic upgrade head` at service start (idempotent). Tests use create_all on sqlite instead.

New schema change workflow:
  docker compose run --rm --no-deps api alembic revision --autogenerate -m "what changed"
  review alembic/versions/<new>.py, commit, deploy: api/worker apply it on start.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parent.parent


def _upgrade() -> None:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    command.upgrade(cfg, "head")


async def upgrade() -> None:
    await asyncio.to_thread(_upgrade)
