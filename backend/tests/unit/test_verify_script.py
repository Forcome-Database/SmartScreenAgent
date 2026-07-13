from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from backend.tests.integration.isolation import (
    CELERY_BINDING_KEY,
    CELERY_QUEUE,
    CELERY_RESULT_PREFIX,
)
from backend.tests.test_bootstrap import TEST_ENV_DEFAULTS
from scripts import verify

REPO_ROOT = Path(__file__).resolve().parents[3]


class RecordingRunner:
    def __init__(self, fail_when: Callable[[list[str]], bool] | None = None) -> None:
        self.calls: list[tuple[list[str], dict[str, str], bool]] = []
        self.fail_when = fail_when or (lambda command: False)

    def __call__(
        self,
        command: list[str],
        *,
        env: dict[str, str],
        capture_output: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((command, env.copy(), capture_output))
        if self.fail_when(command):
            raise subprocess.CalledProcessError(1, command)
        stdout = "3884ec28fea9 (head)\n" if command[-2:] == ["alembic", "current"] else ""
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")


def _commands(runner: RecordingRunner) -> list[list[str]]:
    return [command for command, _env, _capture_output in runner.calls]


def test_partial_up_failure_triggers_down_and_returns_one() -> None:
    runner = RecordingRunner(lambda command: command[-3:] == ["up", "-d", "--wait"])

    result = verify.main([], runner=runner, clean_state_checker=lambda _output, _env: None)

    assert result == 1
    assert _commands(runner)[-1] == [*verify.COMPOSE, "down", "-v", "--remove-orphans"]


def test_direct_script_help_succeeds_without_running_docker() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/verify.py", "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "--keep-services" in result.stdout


def test_success_runs_all_gates_then_down() -> None:
    runner = RecordingRunner()
    clean_state_calls: list[tuple[str, dict[str, str]]] = []

    result = verify.main(
        [],
        runner=runner,
        clean_state_checker=lambda output, env: clean_state_calls.append((output, env.copy())),
    )

    assert result == 0
    assert _commands(runner) == [
        [*verify.COMPOSE, "config", "--quiet"],
        [*verify.COMPOSE, "down", "-v", "--remove-orphans"],
        [*verify.COMPOSE, "up", "-d", "--wait"],
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        [sys.executable, "-m", "pytest", "-m", "not integration", "-q"],
        [sys.executable, "-m", "pytest", "-m", "integration", "-q", "-rs"],
        [sys.executable, "-m", "alembic", "current"],
        [sys.executable, "-m", "ruff", "check", "backend"],
        [
            sys.executable,
            "-m",
            "mypy",
            "--explicit-package-bases",
            "backend/app",
            "--ignore-missing-imports",
        ],
        [*verify.COMPOSE, "down", "-v", "--remove-orphans"],
    ]
    assert clean_state_calls[0][0] == "3884ec28fea9 (head)\n"


def test_clean_state_assertions_run_after_static_checks() -> None:
    events: list[str] = []

    class EventRunner(RecordingRunner):
        def __call__(
            self,
            command: list[str],
            *,
            env: dict[str, str],
            capture_output: bool = False,
        ) -> subprocess.CompletedProcess[str]:
            if "ruff" in command:
                events.append("ruff")
            if "mypy" in command:
                events.append("mypy")
            return super().__call__(command, env=env, capture_output=capture_output)

    runner = EventRunner()

    result = verify.main(
        [],
        runner=runner,
        clean_state_checker=lambda _output, _env: events.append("clean-state"),
    )

    assert result == 0
    assert events == ["ruff", "mypy", "clean-state"]


def test_success_with_keep_services_does_not_run_final_down() -> None:
    runner = RecordingRunner()

    result = verify.main(
        ["--keep-services"],
        runner=runner,
        clean_state_checker=lambda _output, _env: None,
    )

    assert result == 0
    assert _commands(runner).count(
        [*verify.COMPOSE, "down", "-v", "--remove-orphans"]
    ) == 1


def test_cleanup_failure_forces_exit_one() -> None:
    cleanup_count = 0

    def fail_final_cleanup(command: list[str]) -> bool:
        nonlocal cleanup_count
        if command == [*verify.COMPOSE, "down", "-v", "--remove-orphans"]:
            cleanup_count += 1
            return cleanup_count == 2
        return False

    runner = RecordingRunner(fail_final_cleanup)

    result = verify.main([], runner=runner, clean_state_checker=lambda _output, _env: None)

    assert result == 1


def test_developer_environment_is_overwritten(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = RecordingRunner()
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://developer/database")
    monkeypatch.setenv("REDIS_URL", "redis://developer/0")
    monkeypatch.setenv("SMARTSCREEN_REQUIRE_INTEGRATION", "0")

    result = verify.main([], runner=runner, clean_state_checker=lambda _output, _env: None)

    assert result == 0
    child_env = runner.calls[0][1]
    assert {key: child_env[key] for key in TEST_ENV_DEFAULTS} == TEST_ENV_DEFAULTS
    assert child_env["SMARTSCREEN_REQUIRE_INTEGRATION"] == "1"


def test_head_revision_assertion_rejects_non_head_output() -> None:
    with pytest.raises(AssertionError, match="expected Alembic revision"):
        verify.assert_head_revision("older-revision\n")


def test_head_revision_assertion_remains_active_in_optimized_python() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-O",
            "-c",
            "from scripts.verify import assert_head_revision; assert_head_revision('old')",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "AssertionError" in result.stderr


@pytest.mark.asyncio
async def test_postgres_assertion_closes_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    class Connection:
        closed = False

        async def fetch(self, _query: str, _pattern: str) -> list[Any]:
            return []

        async def close(self) -> None:
            self.closed = True

    connection = Connection()

    async def connect(_dsn: str) -> Connection:
        return connection

    monkeypatch.setattr(verify.asyncpg, "connect", connect)

    await verify.assert_no_migration_databases(verify.TEST_ENV)

    assert connection.closed


def test_redis_assertion_reports_wp0_keys_and_closes_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RedisClient:
        closed = False

        def exists(self, *_keys: str) -> int:
            return 1

        def scan_iter(self, *, match: str):
            assert match == f"{CELERY_RESULT_PREFIX}*"
            return iter([f"{CELERY_RESULT_PREFIX}result-id".encode()])

        def close(self) -> None:
            self.closed = True

    client = RedisClient()
    monkeypatch.setattr(verify.Redis, "from_url", lambda _url: client)

    with pytest.raises(AssertionError, match=CELERY_QUEUE):
        verify.assert_no_celery_keys(verify.TEST_ENV)

    assert client.closed


def test_minio_assertion_reports_objects_and_closes_http_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class HttpPool:
        cleared = False

        def clear(self) -> None:
            self.cleared = True

    class MinioClient:
        def list_objects(self, bucket: str, *, recursive: bool):
            assert bucket == TEST_ENV_DEFAULTS["MINIO_BUCKET"]
            assert recursive
            return iter([type("Object", (), {"object_name": "leftover.txt"})()])

    pool = HttpPool()
    monkeypatch.setattr(verify.urllib3, "PoolManager", lambda: pool)
    monkeypatch.setattr(verify, "Minio", lambda **_kwargs: MinioClient())

    with pytest.raises(AssertionError, match="leftover.txt"):
        verify.assert_minio_bucket_empty(verify.TEST_ENV)

    assert pool.cleared


def test_redis_assertion_checks_exact_queue_and_binding_keys() -> None:
    assert CELERY_QUEUE in verify.CELERY_KEYS
    assert CELERY_BINDING_KEY in verify.CELERY_KEYS
