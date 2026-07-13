# WP0 Reproducible Integration Baseline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reproducible local and CI verification path in which PostgreSQL, Redis, MinIO, Alembic, Celery, FastAPI integration tests, unit tests, Ruff, and mypy execute with deterministic configuration and no hidden skips.

**Architecture:** A dedicated Compose file provides isolated test dependencies on non-development ports. Test bootstrap code supplies deterministic defaults without loading the developer `.env`; a strict integration flag converts unavailable dependencies from skips into failures. A cross-platform Python runner owns service startup, verification, and cleanup, and GitHub Actions invokes the same gates.

**Tech Stack:** Python 3.10/3.14, uv, pytest, pytest-asyncio, FastAPI, SQLAlchemy, Alembic, PostgreSQL/pgvector, Redis, Celery, MinIO, Docker Compose, Ruff, mypy, GitHub Actions.

---

## File Structure

- Create: `backend/tests/test_bootstrap.py`
  Defines deterministic test environment defaults and preserves explicit CI overrides.
- Create: `backend/tests/unit/test_test_bootstrap.py`
  Verifies default injection and override preservation without importing application settings.
- Modify: `backend/tests/conftest.py`
  Applies the test environment before any application imports and retains async engine cleanup.
- Create: `backend/tests/fixtures/rule_workbook.py`
  Generates a sanitized six-position workbook so tests do not depend on ignored HR business data.
- Modify: `backend/tests/unit/test_excel_importer.py`
  Uses the generated workbook without conditional skips.
- Modify: `backend/tests/integration/test_cli_import_rules.py`
  Uses the generated workbook without conditional skips.
- Modify: `backend/tests/integration/test_p2_e2e.py`
  Uses the generated workbook without conditional skips.
- Create: `backend/tests/integration/runtime.py`
  Implements strict-or-skip dependency behavior shared by integration fixtures.
- Create: `backend/tests/unit/test_integration_runtime.py`
  Verifies local skip and strict CI failure behavior.
- Modify: `backend/tests/integration/conftest.py`
  Uses strict dependency checks and starts an embedded Celery worker against Redis.
- Move: `backend/tests/unit/test_minio_client.py` -> `backend/tests/integration/test_minio_client.py`
  Places the real MinIO test in the integration suite and removes its private skip policy.
- Modify: `backend/tests/integration/test_smoke.py`
  Runs the Celery broker/worker smoke instead of permanently skipping it.
- Modify: `backend/tests/integration/test_db_migrations.py`
  Replaces the history-only check with a real downgrade/upgrade/current cycle.
- Create: `docker-compose.test.yml`
  Provides isolated disposable PostgreSQL, Redis, and MinIO services.
- Create: `scripts/verify.py`
  Runs the full verification sequence and always cleans up test services unless requested otherwise.
- Create: `.github/workflows/verify.yml`
  Runs unit/static checks on supported Python versions and the full integration runner on Python 3.14.
- Modify: `README.md`
  Documents one-command verification and the difference between local skip mode and strict release mode.
- Modify: `docs/superpowers/specs/2026-07-13-current-state-and-roadmap-design.md`
  Updates WP0 status and evidence only after all exit gates pass.
- Modify: `docs/superpowers/plans/README.md`
  Marks WP0 complete and WP1 ready only after all exit gates pass.

## Task 1: Deterministic Test Environment Bootstrap

**Files:**
- Create: `backend/tests/test_bootstrap.py`
- Create: `backend/tests/unit/test_test_bootstrap.py`
- Modify: `backend/tests/conftest.py`

- [ ] **Step 1: Write tests for defaults and explicit overrides**

Create `backend/tests/unit/test_test_bootstrap.py`:

```python
from backend.tests.test_bootstrap import TEST_ENV_DEFAULTS, apply_test_environment


def test_apply_test_environment_populates_missing_values() -> None:
    environ: dict[str, str] = {}

    apply_test_environment(environ)

    assert environ == TEST_ENV_DEFAULTS


def test_apply_test_environment_preserves_explicit_ci_values() -> None:
    environ = {
        "DATABASE_URL": "postgresql+asyncpg://ci:ci@postgres:5432/ci",
        "REDIS_URL": "redis://redis:6379/1",
    }

    apply_test_environment(environ)

    assert environ["DATABASE_URL"] == "postgresql+asyncpg://ci:ci@postgres:5432/ci"
    assert environ["REDIS_URL"] == "redis://redis:6379/1"
    assert environ["MINIO_ENDPOINT"] == TEST_ENV_DEFAULTS["MINIO_ENDPOINT"]
```

- [ ] **Step 2: Run the new test and verify it fails**

Run:

```bash
uv run pytest backend/tests/unit/test_test_bootstrap.py -v
```

Expected: collection fails with `ModuleNotFoundError: No module named 'backend.tests.test_bootstrap'`.

- [ ] **Step 3: Implement the test environment bootstrap**

Create `backend/tests/test_bootstrap.py`:

```python
from collections.abc import MutableMapping

TEST_ENV_DEFAULTS = {
    "DATABASE_URL": "postgresql+asyncpg://smartscreen:smartscreen@127.0.0.1:55433/smartscreen_test",
    "DATABASE_URL_SYNC": "postgresql://smartscreen:smartscreen@127.0.0.1:55433/smartscreen_test",
    "REDIS_URL": "redis://127.0.0.1:56379/15",
    "MINIO_ENDPOINT": "127.0.0.1:59000",
    "MINIO_ACCESS_KEY": "smartscreen-test",
    "MINIO_SECRET_KEY": "smartscreen-test-secret",
    "MINIO_BUCKET": "resumes-test",
    "MINIO_SECURE": "false",
    "NEWAPI_BASE_URL": "http://127.0.0.1:59999/v1",
    "NEWAPI_API_KEY": "sk-test",
    "LLM_MODEL_EXTRACT": "test-extract",
    "LLM_MODEL_EXTRACT_FALLBACK": "test-extract-fallback",
    "LLM_MODEL_JUDGE": "test-judge",
    "LLM_MODEL_JUDGE_FALLBACK": "test-judge-fallback",
    "LLM_MODEL_LIGHT": "test-light",
    "JWT_SECRET_KEY": "test-secret-do-not-use-in-production",
    "PII_ENCRYPTION_KEY": "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
    "MINERU_MODE": "stub",
}


def apply_test_environment(environ: MutableMapping[str, str]) -> None:
    for key, value in TEST_ENV_DEFAULTS.items():
        environ.setdefault(key, value)
```

Replace the environment-loading block at the top of `backend/tests/conftest.py` with:

```python
import os

from backend.tests.test_bootstrap import apply_test_environment

apply_test_environment(os.environ)
```

Keep the existing `_dispose_db_engine_between_async_tests` fixture unchanged. Remove `dotenv.load_dotenv`, `Path`, the real `.env` lookup, and runtime Fernet key generation from this test conftest.

- [ ] **Step 4: Run bootstrap and existing non-integration tests**

Run:

```bash
uv run pytest backend/tests/unit/test_test_bootstrap.py -v
uv run pytest -m "not integration" -q
```

Expected: 2 bootstrap tests pass and all existing non-integration tests pass without reading repository `.env` values.

- [ ] **Step 5: Commit the deterministic bootstrap**

```bash
git add backend/tests/test_bootstrap.py backend/tests/unit/test_test_bootstrap.py backend/tests/conftest.py
git commit -m "test: isolate test configuration from developer environment"
```

## Task 2: Sanitized Rule Workbook Fixture

**Files:**
- Create: `backend/tests/fixtures/rule_workbook.py`
- Modify: `backend/tests/conftest.py`
- Modify: `backend/tests/unit/test_excel_importer.py`
- Modify: `backend/tests/integration/test_cli_import_rules.py`
- Modify: `backend/tests/integration/test_p2_e2e.py`

- [ ] **Step 1: Replace business-workbook constants with a fixture dependency**

In `backend/tests/unit/test_excel_importer.py`, remove `XLSX` and all three `skipif` decorators. Accept `rules_workbook: Path` in the first three tests and pass that path to `import_workbook`:

```python
def test_imports_all_six_position_sheets(rules_workbook: Path) -> None:
    rules = import_workbook(rules_workbook)
    sheet_jd_codes = {r.jd_code for r in rules}
    assert sheet_jd_codes == set(JD_CODE_BY_SHEET.values())


def test_foreign_trade_rule_has_age_hard_filter(rules_workbook: Path) -> None:
    rules = {r.jd_code: r for r in import_workbook(rules_workbook)}
    ft = rules["FOREIGN_TRADE"]
    age_filters = [h for h in ft.hard_filters if h.audit_tag == "AGE"]
    assert len(age_filters) == 1
    assert "45" in age_filters[0].rule


def test_each_rule_validates_against_schema(rules_workbook: Path) -> None:
    for rule in import_workbook(rules_workbook):
        RuleSchema.model_validate(rule.model_dump())
```

In `backend/tests/integration/test_cli_import_rules.py`, remove `XLSX` and its `skipif` decorator, then change the test signature and invocation:

```python
async def test_cli_import_rules_creates_rule_versions(db_session, rules_workbook: Path):
    runner = CliRunner()
    result = runner.invoke(cli, ["import-rules", str(rules_workbook)])
```

In both tests in `backend/tests/integration/test_p2_e2e.py`, remove `XLSX` and the `skipif` decorators, accept `rules_workbook: Path`, and replace `import_workbook(XLSX)` with `import_workbook(rules_workbook)`.

- [ ] **Step 2: Run importer tests and verify the fixture is missing**

Run:

```bash
uv run pytest backend/tests/unit/test_excel_importer.py -v
```

Expected: the first three tests fail at setup with `fixture 'rules_workbook' not found`.

- [ ] **Step 3: Implement the sanitized workbook builder**

Create `backend/tests/fixtures/rule_workbook.py`:

```python
from pathlib import Path

from openpyxl import Workbook

from backend.app.rules.excel_importer import JD_CODE_BY_SHEET, SHEET_LAYOUT


def build_rule_workbook(path: Path) -> Path:
    workbook = Workbook()
    workbook.remove(workbook.active)

    for sheet_name in JD_CODE_BY_SHEET:
        worksheet = workbook.create_sheet(sheet_name)
        layout = SHEET_LAYOUT[sheet_name]
        dimension_row = layout.data_start_row

        worksheet.cell(dimension_row, 2, "学历")
        worksheet.cell(dimension_row, 3, 100)
        for index, column in enumerate(layout.tier_cols):
            worksheet.cell(dimension_row, column + 1, index * 10)
        worksheet.cell(dimension_row, layout.keyword_col + 1, "本科、专升本、大专")

        total_row = dimension_row + 1
        worksheet.cell(total_row, 2, "合计总分")
        ranges = ("0-39", "40-69", "70-100", "70-84", "85-100")
        for column, score_range in zip(layout.tier_cols, ranges, strict=False):
            worksheet.cell(total_row, column + 1, score_range)

        if sheet_name == "业务岗全维度评分表格":
            worksheet.cell(total_row + 1, 1, "年龄超过45岁直接淘汰")

    workbook.save(path)
    return path
```

Add this fixture to `backend/tests/conftest.py` after applying the test environment:

```python
from pathlib import Path

import pytest

from backend.tests.fixtures.rule_workbook import build_rule_workbook


@pytest.fixture
def rules_workbook(tmp_path: Path) -> Path:
    return build_rule_workbook(tmp_path / "rules.xlsx")
```

Keep the existing async engine disposal fixture below it.

- [ ] **Step 4: Run all importer tests through the generated fixture**

Run:

```bash
uv run pytest backend/tests/unit/test_excel_importer.py -v -rs
```

Expected: all five importer tests pass with no skips. The test module no longer resolves or reads the ignored root HR workbook.

- [ ] **Step 5: Commit the sanitized fixture conversion**

```bash
git add backend/tests/fixtures/rule_workbook.py backend/tests/conftest.py backend/tests/unit/test_excel_importer.py backend/tests/integration/test_cli_import_rules.py backend/tests/integration/test_p2_e2e.py
git commit -m "test: replace private rule workbook with sanitized fixture"
```

## Task 3: Strict Integration Dependency Policy

**Files:**
- Create: `backend/tests/integration/runtime.py`
- Create: `backend/tests/unit/test_integration_runtime.py`
- Modify: `backend/tests/integration/conftest.py`

- [ ] **Step 1: Write strict-mode policy tests**

Create `backend/tests/unit/test_integration_runtime.py`:

```python
import pytest

from backend.tests.integration.runtime import require_service


def test_unavailable_service_skips_in_local_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SMARTSCREEN_REQUIRE_INTEGRATION", raising=False)

    with pytest.raises(pytest.skip.Exception, match="PostgreSQL not reachable"):
        require_service("PostgreSQL", reachable=False)


def test_unavailable_service_fails_in_strict_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMARTSCREEN_REQUIRE_INTEGRATION", "1")

    with pytest.raises(pytest.fail.Exception, match="PostgreSQL not reachable"):
        require_service("PostgreSQL", reachable=False)


def test_available_service_returns_normally(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMARTSCREEN_REQUIRE_INTEGRATION", "1")

    require_service("PostgreSQL", reachable=True)


def test_strict_mode_fails_a_run_with_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.tests.integration.runtime import strict_exit_status

    monkeypatch.setenv("SMARTSCREEN_REQUIRE_INTEGRATION", "1")

    assert strict_exit_status(current_status=0, skipped_count=1) == 1
```

- [ ] **Step 2: Run the policy tests and verify they fail**

Run:

```bash
uv run pytest backend/tests/unit/test_integration_runtime.py -v
```

Expected: collection fails because `backend.tests.integration.runtime` does not exist.

- [ ] **Step 3: Implement strict-or-skip behavior**

Create `backend/tests/integration/runtime.py`:

```python
import os

import pytest

STRICT_INTEGRATION_ENV = "SMARTSCREEN_REQUIRE_INTEGRATION"


def require_service(name: str, *, reachable: bool) -> None:
    if reachable:
        return
    message = f"{name} not reachable"
    if os.getenv(STRICT_INTEGRATION_ENV) == "1":
        pytest.fail(message)
    pytest.skip(message)


def strict_exit_status(*, current_status: int, skipped_count: int) -> int:
    if os.getenv(STRICT_INTEGRATION_ENV) == "1" and skipped_count:
        return int(pytest.ExitCode.TESTS_FAILED)
    return current_status
```

In `backend/tests/integration/conftest.py`, import `require_service` and replace the direct database skip block with:

```python
    require_service("PostgreSQL", reachable=_db_reachable())
```

Keep Alembic upgrade failure as `pytest.fail`; a reachable but broken database must never skip.

Add this session hook to `backend/tests/integration/conftest.py`:

```python
def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    skipped_count = len(reporter.stats.get("skipped", [])) if reporter else 0
    session.exitstatus = strict_exit_status(
        current_status=exitstatus,
        skipped_count=skipped_count,
    )
```

Import `strict_exit_status` beside `require_service`.

- [ ] **Step 4: Run policy tests and local integration selection**

Run:

```bash
uv run pytest backend/tests/unit/test_integration_runtime.py -v
uv run pytest -m integration -q -rs
```

Expected: policy tests pass. With test services stopped, integration tests skip with `PostgreSQL not reachable` rather than erroring.

- [ ] **Step 5: Verify strict mode rejects a missing database**

PowerShell:

```powershell
$env:SMARTSCREEN_REQUIRE_INTEGRATION='1'
uv run pytest backend/tests/integration/test_health.py -v -m integration
Remove-Item Env:SMARTSCREEN_REQUIRE_INTEGRATION
```

Expected: test setup fails with `PostgreSQL not reachable`. This step must be run while the WP0 test PostgreSQL service is stopped.

- [ ] **Step 6: Commit strict integration behavior**

```bash
git add backend/tests/integration/runtime.py backend/tests/unit/test_integration_runtime.py backend/tests/integration/conftest.py
git commit -m "test: fail strict integration runs on missing services"
```

## Task 4: Isolated Disposable Dependency Stack

**Files:**
- Create: `docker-compose.test.yml`

- [ ] **Step 1: Create the isolated Compose stack**

Create `docker-compose.test.yml`:

```yaml
name: smartscreenagent-wp0-test

services:
  postgres:
    image: pgvector/pgvector:pg16@sha256:131dcf7ff6a900545df8e7e092c270aa8c6db2f2c818e408cb45ec21316b74e6
    environment:
      POSTGRES_USER: smartscreen
      POSTGRES_PASSWORD: smartscreen
      POSTGRES_DB: smartscreen_test
    ports:
      - "127.0.0.1:55433:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U smartscreen -d smartscreen_test"]
      interval: 2s
      timeout: 3s
      retries: 20
    tmpfs:
      - /var/lib/postgresql/data

  redis:
    image: redis:7-alpine@sha256:6ab0b6e7381779332f97b8ca76193e45b0756f38d4c0dcda72dbb3c32061ab99
    command: ["redis-server", "--save", "", "--appendonly", "no"]
    ports:
      - "127.0.0.1:56379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 2s
      timeout: 3s
      retries: 20
    tmpfs:
      - /data

  minio:
    image: minio/minio:latest@sha256:14cea493d9a34af32f524e538b8346cf79f3321eff8e708c1e2960462bd8936e
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: smartscreen-test
      MINIO_ROOT_PASSWORD: smartscreen-test-secret
    ports:
      - "127.0.0.1:59000:9000"
      - "127.0.0.1:59001:9001"
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9000/minio/health/live"]
      interval: 2s
      timeout: 3s
      retries: 30
    tmpfs:
      - /data
```

The explicit `smartscreenagent-wp0-test` project name keeps test resources distinct from the development Compose project. Fixed host ports make concurrent WP0 stack runs intentionally unsupported. The test stack intentionally does not mount `infra/postgres/init.sql`: the migration creates the `vector` extension and the current application does not require `pg_trgm`.

- [ ] **Step 2: Validate Compose syntax**

Run:

```bash
docker compose -f docker-compose.test.yml config --quiet
docker compose -f docker-compose.test.yml config --format json
```

Expected: the quiet validation exits 0 with no output. The normalized config reports project name `smartscreenagent-wp0-test`, loopback-only bindings for all four published ports, tmpfs storage for PostgreSQL, Redis, and MinIO, and Redis persistence disabled by its command.

- [ ] **Step 3: Start services and verify health**

Run:

```bash
docker compose -f docker-compose.test.yml up -d --wait
docker compose -f docker-compose.test.yml ps
```

Expected: PostgreSQL, Redis, and MinIO report healthy under project `smartscreenagent-wp0-test`; loopback host ports are 55433, 56379, and 59000/59001.

- [ ] **Step 4: Stop and remove the disposable stack**

Run:

```bash
docker compose -f docker-compose.test.yml down -v --remove-orphans
```

Expected: all `smartscreenagent-wp0-test` containers, tmpfs mounts, and project-specific resources are removed; development services in `docker-compose.yml` are untouched.

- [ ] **Step 5: Commit the test stack**

```bash
git add docker-compose.test.yml
git commit -m "test: add isolated integration dependency stack"
```

## Task 5: Real MinIO, Celery, and Migration Integration Gates

**Files:**
- Move: `backend/tests/unit/test_minio_client.py` -> `backend/tests/integration/test_minio_client.py`
- Modify: `backend/tests/integration/conftest.py`
- Modify: `backend/tests/integration/test_minio_client.py`
- Modify: `backend/tests/integration/test_smoke.py`
- Modify: `backend/tests/integration/test_db_migrations.py`
- Create: `backend/tests/integration/isolation.py`
- Create: `backend/tests/unit/test_integration_isolation.py`

- [ ] **Step 1: Write strict MinIO reachability behavior**

Move the file:

```bash
git mv backend/tests/unit/test_minio_client.py backend/tests/integration/test_minio_client.py
```

Replace its private reachability fixture with:

```python
import io
import socket
from uuid import uuid4

import pytest

from backend.app.config import get_settings
from backend.app.services.storage.minio_client import MinIOStorage
from backend.tests.integration.runtime import require_service

pytestmark = pytest.mark.integration


def _minio_reachable(endpoint: str, timeout: float = 1.5) -> bool:
    host, port_text = endpoint.rsplit(":", 1)
    try:
        with socket.create_connection((host, int(port_text)), timeout=timeout):
            return True
    except OSError:
        return False


@pytest.fixture
def storage() -> MinIOStorage:
    settings = get_settings()
    require_service("MinIO", reachable=_minio_reachable(settings.MINIO_ENDPOINT))
    result = MinIOStorage()
    result.ensure_bucket()
    return result


def test_put_and_get(storage: MinIOStorage) -> None:
    key = f"test/hello-{uuid4().hex}.txt"
    try:
        storage.put_object(key, io.BytesIO(b"hello"), 5, content_type="text/plain")
        assert storage.get_object(key) == b"hello"
    finally:
        storage.delete_object(key)


def test_presigned_url(storage: MinIOStorage) -> None:
    key = f"test/presigned-{uuid4().hex}.txt"
    try:
        storage.put_object(key, io.BytesIO(b"hello"), 5, content_type="text/plain")
        assert storage.presigned_get_url(key, expires_seconds=300).startswith("http")
    finally:
        storage.delete_object(key)
```

- [ ] **Step 2: Add shared isolation helpers and an embedded Celery worker fixture**

Create `backend/tests/integration/isolation.py` with the WP0 Redis namespace, selective cleanup, safe PostgreSQL identifier quoting, and URL derivation used by the worker and migration test:

```python
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.engine import make_url

CELERY_QUEUE = "smartscreen-wp0-test"
CELERY_BINDING_KEY = f"_kombu.binding.{CELERY_QUEUE}"
CELERY_RESULT_PREFIX = f"{CELERY_QUEUE}:"


@dataclass(frozen=True)
class MigrationDatabaseUrls:
    admin_dsn: str
    async_url: str
    sync_url: str


class RedisKeyClient(Protocol):
    def scan_iter(self, *, match: str) -> Iterable[bytes]: ...
    def delete(self, *keys: str | bytes) -> object: ...


def migration_database_urls(
    configured_async_url: str, database_name: str
) -> MigrationDatabaseUrls:
    configured = make_url(configured_async_url)
    admin = configured.set(drivername="postgresql", database="postgres")
    temporary_async = configured.set(database=database_name)
    temporary_sync = configured.set(drivername="postgresql", database=database_name)
    return MigrationDatabaseUrls(
        admin_dsn=admin.render_as_string(hide_password=False),
        async_url=temporary_async.render_as_string(hide_password=False),
        sync_url=temporary_sync.render_as_string(hide_password=False),
    )


def quote_postgres_identifier(identifier: str) -> str:
    escaped = identifier.replace('"', '""')
    return f'"{escaped}"'


def cleanup_celery_keys(client: RedisKeyClient) -> None:
    result_keys = tuple(client.scan_iter(match=f"{CELERY_RESULT_PREFIX}*"))
    client.delete(CELERY_QUEUE, CELERY_BINDING_KEY, *result_keys)
```

Add focused unit coverage in `backend/tests/unit/test_integration_isolation.py` for URL replacement, identifier quoting, and the exact Redis keys selected for deletion.

Append to `backend/tests/integration/conftest.py`:

```python
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
```

Replace the skipped Celery test in `backend/tests/integration/test_smoke.py` with:

```python
def test_celery_ping_when_worker_up(celery_worker) -> None:
    from backend.app.tasks.celery_app import ping

    result = ping.delay()
    try:
        assert result.get(timeout=10) == "pong"
    finally:
        result.forget()
```

- [ ] **Step 3: Replace the migration history smoke with a round trip**

Replace `backend/tests/integration/test_db_migrations.py` with:

```python
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
```

The migration cycle must never target the configured application database. It creates a UUID-named database on the same PostgreSQL server and drops it in `finally`, terminating only sessions attached to that exact temporary database.

- [ ] **Step 4: Start the test stack and run the focused gates**

PowerShell:

```powershell
$env:SMARTSCREEN_REQUIRE_INTEGRATION='1'
try {
    docker compose -f docker-compose.test.yml up -d --wait
    uv run alembic upgrade head
    docker compose -f docker-compose.test.yml exec -T redis redis-cli -n 15 SET wp0-unrelated-proof keep
    uv run pytest backend/tests/integration/test_minio_client.py backend/tests/integration/test_smoke.py backend/tests/integration/test_db_migrations.py -v -m integration
    uv run pytest -m integration -q -rs
    uv run pytest -m "not integration" -q
    # Assert app revision=head, temp DB count=0, WP0 Redis key count=0,
    # unrelated Redis value=keep, and MinIO object count=0.
    docker compose -f docker-compose.test.yml exec -T redis redis-cli -n 15 DEL wp0-unrelated-proof
} finally {
    Remove-Item Env:SMARTSCREEN_REQUIRE_INTEGRATION -ErrorAction SilentlyContinue
    docker compose -f docker-compose.test.yml down -v --remove-orphans
}
```

Expected: MinIO read/write/presign, health, Celery ping, and Alembic round-trip tests pass with zero skips.

After the run, verify the configured application database is still at head, no `smartscreen_migration_%` database remains, no WP0 queue/binding/result key remains, a seeded unrelated Redis key survives until explicitly removed, and the MinIO test bucket is empty. Always put Compose teardown in `finally` while performing these checks.

- [ ] **Step 5: Commit the real integration gates**

```bash
git add -A -- backend/tests/integration backend/tests/unit/test_integration_isolation.py docs/superpowers/plans/2026-07-13-wp0-integration-baseline.md
git commit -m "test: isolate migration and celery integration state"
```

## Task 6: Cross-Platform Verification Runner

**Files:**
- Create: `scripts/verify.py`
- Create: `backend/tests/unit/test_verify_script.py`

- [ ] **Step 1: Write orchestration and cleanup tests**

Create `backend/tests/unit/test_verify_script.py` with a recording command runner and no
real Docker or network calls. Cover at least:

- Compose config failure returns 1 without attempting teardown;
- proactive Compose teardown failure returns 1 without a second teardown attempt;
- partial `docker compose up -d --wait` failure still runs teardown and returns 1;
- successful verification runs teardown and returns 0;
- `--keep-services` suppresses only the final teardown after full success;
- teardown failure forces exit 1;
- developer environment values are overwritten by the deterministic test environment;
- hostile inherited Compose project names are overwritten in the child environment and cannot
  override the explicit safe project name on any Compose command;
- the exact gate order uses `sys.executable -m`, with post-gate clean-state assertions;
- clean-state failures remain active when Python optimization is enabled;
- PostgreSQL, Redis, and MinIO assertion clients are closed even when an assertion fails.

Run:

```bash
uv run pytest backend/tests/unit/test_verify_script.py -q
```

Expected before implementation: collection fails because `scripts.verify` does not exist.

- [ ] **Step 2: Implement the safe verification runner**

Create `scripts/verify.py` with these implementation constraints:

```python
COMPOSE_PROJECT_NAME = "smartscreenagent-wp0-test"
COMPOSE_FILE = REPO_ROOT / "docker-compose.test.yml"
COMPOSE = [
    "docker",
    "compose",
    "--project-name",
    COMPOSE_PROJECT_NAME,
    "--project-directory",
    str(REPO_ROOT),
    "-f",
    str(COMPOSE_FILE),
]
TEST_ENV = {
    **TEST_ENV_DEFAULTS,
    "SMARTSCREEN_REQUIRE_INTEGRATION": "1",
    "COMPOSE_PROJECT_NAME": COMPOSE_PROJECT_NAME,
}
```

Copy the parent environment and overwrite it with `TEST_ENV`; never allow developer values to
redirect this disposable gate. Pin the Compose identity at command-line precedence and use the
absolute Compose file/project directory so the caller's working directory and Compose environment
cannot change the target project. Accept an injectable command runner and clean-state checker for
unit tests. Parse arguments before any Docker command so `--help` is mutation-free.

Run these commands in order:

1. `[*COMPOSE, "config", "--quiet"]`
2. `[*COMPOSE, "down", "-v", "--remove-orphans"]`
3. `[*COMPOSE, "up", "-d", "--wait"]`
4. `sys.executable -m alembic upgrade head`
5. `sys.executable -m pytest -m "not integration" -q`
6. `sys.executable -m pytest -m integration -q -rs`
7. `sys.executable -m alembic current` with captured output
8. `sys.executable -m ruff check backend`
9. `sys.executable -m mypy --explicit-package-bases backend/app --ignore-missing-imports`

After all gates, assert before teardown that Alembic output contains `3884ec28fea9`, PostgreSQL
has no database for which `starts_with(datname, 'smartscreen_migration_')` is true, Redis has
neither the WP0 queue/binding keys nor any WP0 result-prefix key, and the MinIO test bucket has no
objects. Use
`TEST_ENV_DEFAULTS` and the existing integration isolation constants/helpers. Close every client.

Catch `OSError`, `subprocess.CalledProcessError`, and clean-state assertion failures, print a
concise error, and return 1. Keep cleanup disabled through Compose validation and proactive
teardown, then set `cleanup_required = True` immediately before invoking Compose `up`. Once that
marker is set, execute checked teardown after every failure and after normal success unless
`--keep-services` was requested. Config/proactive-teardown failures do not run a redundant final
teardown; partial startup failures do. A teardown failure always returns 1.

Run:

```bash
uv run pytest backend/tests/unit/test_verify_script.py -q
uv run ruff check scripts/verify.py backend/tests/unit/test_verify_script.py
```

Expected: all focused tests and Ruff pass without starting Docker.

- [ ] **Step 3: Verify runner help without Docker mutation**

Run:

```bash
uv run python scripts/verify.py --help
```

Expected: usage text lists `--keep-services` and exits 0.

- [ ] **Step 4: Run the complete local verification path**

Run:

```powershell
$env:COMPOSE_PROJECT_NAME = "developer-project"
try {
    uv run python scripts/verify.py
} finally {
    Remove-Item Env:COMPOSE_PROJECT_NAME -ErrorAction SilentlyContinue
}
```

Expected: Compose validation, dependency startup, migration, non-integration tests, integration
tests, post-integration revision check, Ruff, mypy, and all four clean-state assertions pass.
Integration output contains no skipped tests. The test Compose project is absent afterward and
unrelated containers are unchanged. No `developer-project` Compose command or resource is created,
changed, or removed.

- [ ] **Step 5: Commit the verification runner and tests**

```bash
git add scripts/verify.py backend/tests/unit/test_verify_script.py docs/superpowers/plans/2026-07-13-wp0-integration-baseline.md
git commit -m "test: add one-command full verification runner"
```

## Task 7: GitHub Actions Verification

**Files:**
- Create: `.github/workflows/verify.yml`

- [ ] **Step 1: Create the workflow**

Create `.github/workflows/verify.yml`:

```yaml
name: verify

on:
  push:
    branches: [main]
  pull_request:

permissions:
  contents: read

jobs:
  unit-and-static:
    strategy:
      fail-fast: false
      matrix:
        python-version: ["3.10", "3.14"]
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5 # v4
      - uses: astral-sh/setup-uv@d0cc045d04ccac9d8b7881df0226f9e82c39688e # v6
        with:
          python-version: ${{ matrix.python-version }}
          enable-cache: true
      - run: uv sync --extra dev --locked
      - run: uv run pytest -m "not integration" -q
      - run: uv run ruff check backend
      - run: uv run mypy --explicit-package-bases backend/app --ignore-missing-imports

  integration:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5 # v4
      - uses: astral-sh/setup-uv@d0cc045d04ccac9d8b7881df0226f9e82c39688e # v6
        with:
          python-version: "3.14"
          enable-cache: true
      - run: uv sync --extra dev --locked
      - run: uv run python scripts/verify.py
```

GitHub Actions is the initial hosted runner because the repository has no existing CI provider. The authoritative entry point remains `scripts/verify.py`, so another provider can invoke the same command without changing test semantics.

- [ ] **Step 2: Validate workflow syntax and local parity**

Run:

```bash
uv run python scripts/verify.py
git diff --check
```

Expected: full verification passes and Git reports no whitespace errors. Hosted workflow success remains required once a GitHub remote exists.

- [ ] **Step 3: Commit the workflow**

```bash
git add .github/workflows/verify.yml
git commit -m "ci: verify supported Python and integration stack"
```

## Task 8: Documentation and WP0 Exit Review

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-07-13-current-state-and-roadmap-design.md`
- Modify: `docs/superpowers/plans/README.md`
- Modify: `docs/superpowers/plans/2026-07-13-wp0-integration-baseline.md`

- [ ] **Step 1: Add verification commands to README**

Replace the existing development-verification block with:

````markdown
## 开发验证

```bash
# 离线单元测试与静态检查
uv run pytest -m "not integration"
uv run ruff check backend
uv run mypy --explicit-package-bases backend/app --ignore-missing-imports

# 严格全量验证：启动隔离依赖、执行迁移/集成测试并自动清理
uv run python scripts/verify.py
```

直接运行 `pytest -m integration` 时，缺少外部服务会跳过相关用例，适合本地快速选择；`scripts/verify.py` 设置严格模式，任何依赖缺失或测试跳过都视为发布门禁失败。
````

- [ ] **Step 2: Run the final exit gates**

Run:

```bash
uv sync --extra dev --locked
uv run python scripts/verify.py
git diff --check
git status --short --branch
```

Expected:

- 72 non-integration tests pass: the original 66 plus six WP0 policy/bootstrap tests.
- All 16 integration tests execute and pass with zero skips.
- PostgreSQL migration downgrade/upgrade succeeds.
- MinIO read/write/presign and Celery ping succeed.
- Ruff and mypy pass.
- Only WP0 files are modified before the final commit.

- [ ] **Step 3: Record completion evidence**

In `docs/superpowers/specs/2026-07-13-current-state-and-roadmap-design.md`:

- Change WP0 from planned to complete in the traceability/status text.
- Add the final test counts and the successful GitHub Actions run URL. If the repository has not been connected to GitHub, stop here and do not mark WP0 complete.
- Keep WP1 scope unchanged.

In `docs/superpowers/plans/README.md`:

- Mark WP0 `Complete`.
- Mark WP1 `Ready`.
- Link the approved WP1 implementation plan only after that plan is written.

In this plan, check every executed step and append a short `## Completion Evidence` section containing the commit range and exact verification command output summary.

- [ ] **Step 4: Commit WP0 documentation and completion status**

```bash
git add README.md docs/superpowers/specs/2026-07-13-current-state-and-roadmap-design.md docs/superpowers/plans/README.md docs/superpowers/plans/2026-07-13-wp0-integration-baseline.md
git commit -m "docs: record reproducible integration baseline"
```

## Plan Self-Review Checklist

- [x] WP0 spec coverage: disposable dependencies, migration verification, real PostgreSQL/Redis/MinIO/Celery execution, deterministic fixtures, and one local command all map to explicit tasks.
- [x] Strict mode makes a missing dependency fail instead of skip.
- [x] No task changes application business behavior.
- [x] Test database and ports do not collide with the development Compose defaults.
- [x] Every code change includes exact content, an execution command, an expected result, and a commit boundary.
- [x] The final exit review updates the roadmap and unlocks WP1 only after evidence exists.
