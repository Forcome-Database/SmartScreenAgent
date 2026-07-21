from __future__ import annotations

import asyncio
import subprocess
import time
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
    "ingestion_jobs",
    "audit_logs",
    "feedback",
    "scores",
    "candidate_embeddings",
    "candidates",
    "rule_versions",
    "jds",
    "golden_set",
    "users",
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


@pytest_asyncio.fixture
async def stored_job_factory(db_session):
    """Insert an `IngestionJob` row with dummy-but-valid raw_file_* columns.

    Each call gets a fresh, unique sha256 (64 hex chars) so repeated
    invocations within a test never collide with the partial unique index
    on `raw_file_sha256` (`uq_ingestion_jobs_sha256_active`).
    """
    import hashlib
    from uuid import uuid4

    from backend.app.models import IngestionJob
    from backend.app.services.ingestion.states import IngestionState

    async def _make(
        *,
        state: IngestionState,
        attempts: int = 0,
        lease_expires_at=None,
    ) -> IngestionJob:
        sha256 = hashlib.sha256(uuid4().bytes).hexdigest()
        job = IngestionJob(
            state=state.value,
            source="upload",
            source_external_id=None,
            jd_code=None,
            raw_file_key=f"resumes/test/sweeper-{sha256[:16]}",
            raw_file_sha256=sha256,
            raw_file_size_bytes=1234,
            raw_file_content_type="application/pdf",
            raw_file_original_name_cipher="cipher",
            attempts=attempts,
            lease_expires_at=lease_expires_at,
            actor="test",
        )
        db_session.add(job)
        await db_session.flush()
        return job

    return _make


@pytest_asyncio.fixture
async def auth_headers(db_session):
    """Create a real database user and JWT for route-level authorization tests."""
    from uuid import uuid4

    from backend.app.models import User
    from backend.app.security.jwt import create_access_token

    async def _make(role: str = "hr", *, token_role: str | None = None) -> dict[str, str]:
        user = User(
            dingtalk_userid=f"test-{uuid4().hex}",
            display_name=f"Test {role}",
            role=role,
        )
        db_session.add(user)
        await db_session.commit()
        token = create_access_token(
            {"sub": str(user.id), "role": token_role if token_role is not None else role}
        )
        return {"Authorization": f"Bearer {token}"}

    return _make


@pytest.fixture
def minio_storage():
    """Real isolated MinIO client with post-test resume-object cleanup."""
    import socket

    from backend.app.config import get_settings
    from backend.app.services.storage.minio_client import MinIOStorage

    settings = get_settings()
    host, port_text = settings.MINIO_ENDPOINT.rsplit(":", 1)
    try:
        with socket.create_connection((host, int(port_text)), timeout=1.5):
            pass
        reachable = True
    except OSError:
        reachable = False
    require_service("MinIO", reachable=reachable)

    storage = MinIOStorage()
    storage.ensure_bucket()
    try:
        yield storage
    finally:
        for key in storage.list_object_keys(prefix="resumes/"):
            storage.delete_object(key)


@pytest.fixture
def valid_pdf_bytes() -> bytes:
    import io

    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    stream = io.BytesIO()
    writer.write(stream)
    return stream.getvalue()


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


@pytest_asyncio.fixture
async def poll_job(client):
    """Poll `GET /candidates/jobs/{id}` until `state` lands in `until`.

    Fails the test outright — rather than looping forever or silently
    passing — if the job reaches `terminal_failed` while `until` does not
    expect it, or if `timeout` elapses first. A durability test that merely
    waited and asserted afterward could pass on a hung worker as easily as a
    working one; failing fast here makes that failure mode visible.
    """

    async def _poll(
        job_id: int,
        headers: dict[str, str],
        *,
        until: set[str],
        timeout: float = 30.0,
        interval: float = 0.2,
    ) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            resp = await client.get(f"/api/v1/candidates/jobs/{job_id}", headers=headers)
            assert resp.status_code == 200, resp.text
            body = resp.json()
            state = body["state"]
            if state in until:
                return body
            if state == "terminal_failed" and "terminal_failed" not in until:
                pytest.fail(
                    f"ingestion job {job_id} reached terminal_failed "
                    f"(error_code={body.get('last_error_code')!r}) while polling for {until}"
                )
            if time.monotonic() >= deadline:
                pytest.fail(
                    f"ingestion job {job_id} did not reach {until} within {timeout}s "
                    f"(last observed state={state!r})"
                )
            await asyncio.sleep(interval)

    return _poll


@pytest_asyncio.fixture
async def run_one_sweep():
    """Run one `ingest.sweep` pass synchronously, then hand any requeued job
    ids to Celery exactly as `sweep_task` does — without needing Beat itself
    running in-process for tests.

    Uses its own `AsyncSessionLocal()` (not the test's `db_session`) and
    disposes the shared async engine pool before calling `.delay()`, mirroring
    the asyncpg loop-affinity hazard already documented in
    `test_tasks_ingest.py::test_celery_task_downloads_verified_object`: the
    `celery_worker` fixture runs the worker in a background thread with its
    own event loop, and handing it a connection checked out on the main
    pytest-asyncio loop would crash it with "attached to a different loop".

    Returns `(report, async_results)`: `async_results` is the list of
    `AsyncResult` handles from each `.delay()` call above, one per
    `report.requeued` job id and in the same order, so a caller can
    `.get(timeout=...)` them to block until the worker actually finishes
    each re-enqueued job — instead of resorting to concurrent HTTP polling
    against the same shared engine, which carries the identical cross-loop
    hazard while the worker is still running.
    """
    from datetime import datetime, timezone

    from celery.result import AsyncResult

    from backend.app.config import get_settings
    from backend.app.database import AsyncSessionLocal, engine
    from backend.app.services.ingestion.sweeper import SweepReport, sweep
    from backend.app.tasks.ingest import parse_and_score_task

    async def _run() -> tuple[SweepReport, list[AsyncResult]]:
        settings = get_settings()
        async with AsyncSessionLocal() as db:
            report = await sweep(
                db,
                now=datetime.now(timezone.utc),
                max_attempts=settings.INGESTION_MAX_ATTEMPTS,
            )
            await db.commit()
        await engine.dispose()
        async_results = [parse_and_score_task.delay(job_id) for job_id in report.requeued]
        return report, async_results

    return _run
