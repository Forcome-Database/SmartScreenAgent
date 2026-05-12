import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from backend.app.config import get_settings

# ⚠️ Task 3-only workaround: backend.app.models doesn't exist yet (Task 4 creates it).
# Use a try/except so `alembic history`/`alembic --help` works in Task 3 before
# Task 4 lands. Once Task 4 creates Base, this import will succeed and autogenerate
# will see the metadata. Do NOT remove the try/except — it's a defensive pattern
# that also helps if the models package is refactored later.
try:
    from backend.app.models import Base  # noqa: F401
    target_metadata = Base.metadata
except ImportError:
    target_metadata = None

config = context.config
config.set_main_option("sqlalchemy.url", get_settings().DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    raise NotImplementedError("Offline mode not supported")
else:
    run_migrations_online()
