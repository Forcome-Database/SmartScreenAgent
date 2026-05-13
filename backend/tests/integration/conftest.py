from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import pytest
import pytest_asyncio
from sqlalchemy import text


REPO_ROOT = Path(__file__).resolve().parents[3]


def _db_reachable() -> bool:
    """Best-effort TCP ping of the configured Postgres host."""
    try:
        import asyncpg  # noqa: F401

        from backend.app.config import get_settings

        url = urlparse(
            get_settings().DATABASE_URL.replace("postgresql+asyncpg", "postgresql")
        )

        async def _ping() -> bool:
            try:
                import asyncpg as _pg

                conn = await _pg.connect(
                    user=url.username,
                    password=url.password,
                    database=url.path.lstrip("/"),
                    host=url.hostname,
                    port=url.port or 5432,
                    timeout=2,
                )
                await conn.close()
                return True
            except Exception:
                return False

        return asyncio.run(_ping())
    except Exception:
        return False


@pytest.fixture(scope="session", autouse=True)
def _apply_migrations():
    """Run `alembic upgrade head` once per session; skip session if DB unreachable."""
    if not _db_reachable():
        pytest.skip(
            "PostgreSQL not reachable; skipping integration tests",
            allow_module_level=False,
        )
    result = subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=90,
    )
    if result.returncode != 0:
        pytest.fail(f"alembic upgrade head failed:\n{result.stderr}\n{result.stdout}")
    yield


# Children before parents — FK-safe TRUNCATE order.
_CLEAN_TABLES = [
    "audit_logs",
    "feedback",
    "scores",
    "candidate_embeddings",
    "candidates",
    "rule_versions",
    "jds",
    "golden_set",
]


@pytest_asyncio.fixture
async def db_session():
    """Function-scoped fresh AsyncSession; truncates P2 tables on teardown."""
    from backend.app.database import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            try:
                # JDs have an FK to rule_versions (active_rule_version_id) and
                # rule_versions have an FK to jds — TRUNCATE ... CASCADE handles
                # the cycle. We still iterate in a sensible order in case CASCADE
                # is somehow disabled.
                for tbl in _CLEAN_TABLES:
                    await session.execute(
                        text(f'TRUNCATE TABLE "{tbl}" RESTART IDENTITY CASCADE')
                    )
                await session.commit()
            except Exception:
                await session.rollback()
