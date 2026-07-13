from backend.app.config import Settings
from backend.tests.test_bootstrap import TEST_ENV_DEFAULTS, apply_test_environment


def test_defaults_cover_every_settings_field() -> None:
    assert set(TEST_ENV_DEFAULTS) == set(Settings.model_fields)


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


def test_apply_test_environment_overwrites_behavior_settings() -> None:
    environ = {
        "JWT_ALGORITHM": "RS256",
        "MINERU_MODE": "http",
        "CORS_ORIGINS": "https://hostile.example",
    }

    apply_test_environment(environ)

    assert environ["JWT_ALGORITHM"] == "HS256"
    assert environ["MINERU_MODE"] == "stub"
    assert environ["CORS_ORIGINS"] == "http://localhost:3000"
