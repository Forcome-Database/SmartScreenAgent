from __future__ import annotations

import asyncio
import subprocess
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlparse

import pytest
import pytest_asyncio
from sqlalchemy import text

from backend.tests.integration.runtime import require_service

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
    require_service("PostgreSQL", reachable=_db_reachable())
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


@pytest_asyncio.fixture
async def client():
    """Async HTTP client bound to the FastAPI app via ASGITransport."""
    import httpx

    from backend.app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


@pytest.fixture(scope="session")
def celery_worker() -> Iterator[None]:
    from celery.contrib.testing.worker import start_worker
    from redis import Redis

    from backend.app.config import get_settings
    from backend.app.tasks.celery_app import celery_app
    from backend.tests.integration.isolation import (
        CELERY_QUEUE,
        CELERY_RESULT_PREFIX,
        cleanup_celery_keys,
    )

    redis_client = Redis.from_url(get_settings().REDIS_URL)
    previous_queue = celery_app.conf.task_default_queue
    previous_backend_options = celery_app.conf.result_backend_transport_options
    previous_backend_cache = celery_app._backend_cache
    backend_missing = object()
    previous_local_backend = getattr(celery_app._local, "backend", backend_missing)
    try:
        cleanup_celery_keys(redis_client)
        backend_options = dict(previous_backend_options or {})
        backend_options["global_keyprefix"] = CELERY_RESULT_PREFIX
        celery_app.conf.update(
            task_default_queue=CELERY_QUEUE,
            result_backend_transport_options=backend_options,
        )
        # Celery caches backends separately for thread-safe and thread-local use.
        celery_app._backend_cache = None
        if previous_local_backend is not backend_missing:
            del celery_app._local.backend
        with start_worker(
            celery_app,
            pool="solo",
            perform_ping_check=False,
            queues=[CELERY_QUEUE],
        ):
            yield
    finally:
        try:
            cleanup_celery_keys(redis_client)
        finally:
            try:
                celery_app.conf.update(
                    task_default_queue=previous_queue,
                    result_backend_transport_options=previous_backend_options,
                )
                celery_app._backend_cache = previous_backend_cache
                if previous_local_backend is backend_missing:
                    if hasattr(celery_app._local, "backend"):
                        del celery_app._local.backend
                else:
                    celery_app._local.backend = previous_local_backend
            finally:
                redis_client.close()
