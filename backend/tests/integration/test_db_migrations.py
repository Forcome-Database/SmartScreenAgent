import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

from backend.app.config import get_settings
from backend.tests.integration.isolation import (
    migration_database_urls,
    quote_postgres_identifier,
)

pytestmark = pytest.mark.integration
REPO_ROOT = Path(__file__).resolve().parents[3]


def _alembic(*args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        capture_output=True,
        text=True,
        timeout=90,
        cwd=REPO_ROOT,
        env=env,
    )


async def _create_database(admin_dsn: str, database_name: str) -> None:
    connection = await asyncpg.connect(admin_dsn)
    try:
        await connection.execute(
            f"CREATE DATABASE {quote_postgres_identifier(database_name)}"
        )
    finally:
        await connection.close()


async def _drop_database(admin_dsn: str, database_name: str) -> None:
    connection = await asyncpg.connect(admin_dsn)
    try:
        await connection.execute(
            """
            SELECT pg_terminate_backend(pid)
            FROM pg_stat_activity
            WHERE datname = $1 AND pid <> pg_backend_pid()
            """,
            database_name,
        )
        await connection.execute(
            f"DROP DATABASE IF EXISTS {quote_postgres_identifier(database_name)}"
        )
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_alembic_round_trip_from_base() -> None:
    database_name = f"smartscreen_migration_{uuid4().hex}"
    urls = migration_database_urls(get_settings().DATABASE_URL, database_name)
    env = os.environ.copy()
    env["DATABASE_URL"] = urls.async_url
    env["DATABASE_URL_SYNC"] = urls.sync_url

    try:
        await _create_database(urls.admin_dsn, database_name)
        downgrade = _alembic("downgrade", "base", env=env)
        assert downgrade.returncode == 0, downgrade.stderr
        upgrade = _alembic("upgrade", "head", env=env)
        assert upgrade.returncode == 0, upgrade.stderr
        current = _alembic("current", env=env)
        assert current.returncode == 0, current.stderr
        assert "3884ec28fea9" in current.stdout
    finally:
        await _drop_database(urls.admin_dsn, database_name)
