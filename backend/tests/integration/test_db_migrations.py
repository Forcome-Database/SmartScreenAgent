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
BASELINE_REVISION = "3884ec28fea9"
WP1_REVISION = "b57c2f9e1a6d"
WP3_HEAD_REVISION = "1e9b39dbf340"


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
        assert WP3_HEAD_REVISION in current.stdout
    finally:
        await _drop_database(urls.admin_dsn, database_name)


@pytest.mark.asyncio
async def test_wp1_migration_preserves_legacy_candidate() -> None:
    database_name = f"smartscreen_migration_{uuid4().hex}"
    urls = migration_database_urls(get_settings().DATABASE_URL, database_name)
    env = os.environ.copy()
    env["DATABASE_URL"] = urls.async_url
    env["DATABASE_URL_SYNC"] = urls.sync_url

    try:
        await _create_database(urls.admin_dsn, database_name)
        baseline = _alembic("upgrade", BASELINE_REVISION, env=env)
        assert baseline.returncode == 0, baseline.stderr

        connection = await asyncpg.connect(urls.sync_url)
        try:
            await connection.execute(
                """
                INSERT INTO candidates (
                    source, name_cipher, raw_file_key, pii_hash
                ) VALUES ($1, $2, $3, $4)
                """,
                "upload",
                "encrypted-name",
                "C:/legacy/tmp/resume.pdf",
                "a" * 64,
            )
        finally:
            await connection.close()

        upgrade = _alembic("upgrade", WP1_REVISION, env=env)
        assert upgrade.returncode == 0, upgrade.stderr

        connection = await asyncpg.connect(urls.sync_url)
        try:
            row = await connection.fetchrow(
                """
                SELECT raw_file_key, raw_file_sha256, raw_file_size_bytes,
                       raw_file_content_type, raw_file_original_name_cipher
                FROM candidates
                WHERE pii_hash = $1
                """,
                "a" * 64,
            )
            assert row is not None
            assert row["raw_file_key"] == "C:/legacy/tmp/resume.pdf"
            assert row["raw_file_sha256"] is None
            assert row["raw_file_size_bytes"] is None
            assert row["raw_file_content_type"] is None
            assert row["raw_file_original_name_cipher"] is None
        finally:
            await connection.close()

        downgrade = _alembic("downgrade", BASELINE_REVISION, env=env)
        assert downgrade.returncode == 0, downgrade.stderr
    finally:
        await _drop_database(urls.admin_dsn, database_name)
