"""Alembic environment (async engine). Autogenerate compares core.db.Base with the live database."""
import asyncio

from alembic import context
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from core.config import get_secrets, get_settings
from core.db import Base, db_url

target_metadata = Base.metadata


def _run(connection):
    # api and worker both migrate at start-up; a Postgres advisory lock makes the second one wait
    if connection.dialect.name == "postgresql":
        connection.execute(text("select pg_advisory_lock(7430211)"))
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()
    if connection.dialect.name == "postgresql":
        connection.execute(text("select pg_advisory_unlock(7430211)"))
        connection.commit()


async def _main():
    engine = create_async_engine(db_url(get_settings(), get_secrets()))
    async with engine.connect() as conn:
        await conn.run_sync(_run)
    await engine.dispose()


asyncio.run(_main())
