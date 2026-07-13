from collections.abc import MutableMapping

TEST_ENV_DEFAULTS = {
    "DATABASE_URL": "postgresql+asyncpg://smartscreen:smartscreen@127.0.0.1:55432/smartscreen_test",
    "DATABASE_URL_SYNC": "postgresql://smartscreen:smartscreen@127.0.0.1:55432/smartscreen_test",
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
