from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import asyncpg  # noqa: E402
import urllib3  # noqa: E402
from minio import Minio  # noqa: E402
from redis import Redis  # noqa: E402

from backend.tests.integration.isolation import (  # noqa: E402
    CELERY_BINDING_KEY,
    CELERY_QUEUE,
    CELERY_RESULT_PREFIX,
    migration_database_urls,
)
from backend.tests.test_bootstrap import TEST_ENV_DEFAULTS  # noqa: E402

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
HEAD_REVISION = "2f27938b430b"
CELERY_KEYS = (CELERY_QUEUE, CELERY_BINDING_KEY)

CommandRunner = Callable[..., subprocess.CompletedProcess[str]]
CleanStateChecker = Callable[[str, dict[str, str]], None]


def run(
    command: list[str],
    *,
    env: dict[str, str],
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    print(f"+ {' '.join(command)}", flush=True)
    return subprocess.run(
        command,
        cwd=REPO_ROOT,
        env=env,
        check=True,
        capture_output=capture_output,
        text=True,
    )


def assert_head_revision(output: str) -> None:
    if HEAD_REVISION not in output:
        actual = output.strip() or "no output"
        raise AssertionError(f"expected Alembic revision {HEAD_REVISION} at head; got {actual}")


async def assert_no_migration_databases(env: dict[str, str]) -> None:
    admin_dsn = migration_database_urls(env["DATABASE_URL"], "postgres").admin_dsn
    connection = await asyncpg.connect(admin_dsn)
    try:
        rows = await connection.fetch(
            "SELECT datname FROM pg_database WHERE starts_with(datname, $1) ORDER BY datname",
            "smartscreen_migration_",
        )
        leftovers = [row["datname"] for row in rows]
        if leftovers:
            raise AssertionError(f"temporary migration databases remain: {', '.join(leftovers)}")
    finally:
        await connection.close()


async def assert_application_tables_empty(env: dict[str, str]) -> None:
    dsn = env["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    connection = await asyncpg.connect(dsn)
    try:
        for table in ("audit_logs", "scores", "candidates", "users", "ingestion_jobs"):
            count = await connection.fetchval(f'SELECT count(*) FROM "{table}"')
            if count:
                raise AssertionError(f"application table {table} contains {count} rows")
    finally:
        await connection.close()


def assert_no_celery_keys(env: dict[str, str]) -> None:
    client = Redis.from_url(env["REDIS_URL"])
    try:
        leftovers = [key for key in CELERY_KEYS if client.exists(key)]
        result_keys = tuple(client.scan_iter(match=f"{CELERY_RESULT_PREFIX}*"))
        leftovers.extend(
            key.decode(errors="replace") if isinstance(key, bytes) else str(key)
            for key in result_keys
        )
        if leftovers:
            raise AssertionError(f"WP0 Celery Redis keys remain: {', '.join(leftovers)}")
    finally:
        client.close()


def assert_minio_bucket_empty(env: dict[str, str]) -> None:
    http_client = urllib3.PoolManager()
    client = Minio(
        endpoint=env["MINIO_ENDPOINT"],
        access_key=env["MINIO_ACCESS_KEY"],
        secret_key=env["MINIO_SECRET_KEY"],
        secure=env["MINIO_SECURE"].lower() == "true",
        http_client=http_client,
    )
    try:
        objects = list(client.list_objects(env["MINIO_BUCKET"], recursive=True))
        names = [item.object_name or "<unnamed>" for item in objects]
        if names:
            raise AssertionError(f"MinIO test bucket contains objects: {', '.join(names)}")
    finally:
        http_client.clear()


def assert_no_temp_uploads() -> None:
    temp_dir = Path(tempfile.gettempdir())
    leftovers = sorted(
        path.name
        for pattern in (
            "smartscreen-upload-*",
            "smartscreen-worker-*",
            "smartscreen-mineru-*",
        )
        for path in temp_dir.glob(pattern)
    )
    if leftovers:
        raise AssertionError(f"temporary resume files remain: {', '.join(leftovers)}")


def assert_clean_state(current_output: str, env: dict[str, str]) -> None:
    try:
        assert_head_revision(current_output)
        asyncio.run(assert_no_migration_databases(env))
        asyncio.run(assert_application_tables_empty(env))
        assert_no_celery_keys(env)
        assert_minio_bucket_empty(env)
        assert_no_temp_uploads()
    except AssertionError:
        raise
    except Exception as exc:
        raise AssertionError(f"clean-state check could not complete: {exc}") from exc
    print("Clean-state assertions passed: Alembic, PostgreSQL, Redis, MinIO", flush=True)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run SmartScreenAgent verification gates")
    parser.add_argument(
        "--keep-services",
        action="store_true",
        help="leave the disposable test services running after successful verification",
    )
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    runner: CommandRunner | None = None,
    clean_state_checker: CleanStateChecker | None = None,
) -> int:
    args = parse_args(argv)
    command_runner = runner or run
    state_checker = clean_state_checker or assert_clean_state
    env = os.environ.copy()
    env.update(TEST_ENV)
    cleanup_required = False
    verification_succeeded = False
    exit_code = 0

    try:
        command_runner([*COMPOSE, "config", "--quiet"], env=env)
        command_runner([*COMPOSE, "down", "-v", "--remove-orphans"], env=env)
        cleanup_required = True
        command_runner([*COMPOSE, "up", "-d", "--wait"], env=env)
        command_runner([sys.executable, "-m", "alembic", "upgrade", "head"], env=env)
        command_runner(
            [
                sys.executable,
                "-m",
                "pytest",
                "-m",
                "not integration and not external_contract",
                "-q",
            ],
            env=env,
        )
        command_runner(
            [sys.executable, "-m", "pytest", "-m", "integration", "-q", "-rs"],
            env=env,
        )
        current = command_runner(
            [sys.executable, "-m", "alembic", "current"],
            env=env,
            capture_output=True,
        )
        command_runner([sys.executable, "-m", "ruff", "check", "backend"], env=env)
        command_runner(
            [
                sys.executable,
                "-m",
                "mypy",
                "--explicit-package-bases",
                "backend/app",
                "--ignore-missing-imports",
            ],
            env=env,
        )
        state_checker(current.stdout, env)
        verification_succeeded = True
    except (OSError, subprocess.CalledProcessError, AssertionError) as exc:
        print(f"verification failed: {exc}", file=sys.stderr)
        exit_code = 1
    finally:
        if cleanup_required and (not verification_succeeded or not args.keep_services):
            try:
                command_runner(
                    [*COMPOSE, "down", "-v", "--remove-orphans"],
                    env=env,
                )
            except (OSError, subprocess.CalledProcessError) as exc:
                print(f"cleanup failed: {exc}", file=sys.stderr)
                exit_code = 1

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
