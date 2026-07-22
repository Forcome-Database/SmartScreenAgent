from collections.abc import MutableMapping

TEST_ENV_DEFAULTS = {
    "DATABASE_URL": "postgresql+asyncpg://smartscreen:smartscreen@127.0.0.1:55433/smartscreen_test",
    "DATABASE_URL_SYNC": "postgresql://smartscreen:smartscreen@127.0.0.1:55433/smartscreen_test",
    "REDIS_URL": "redis://127.0.0.1:56379/15",
    "MINIO_ENDPOINT": "127.0.0.1:61000",
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
    "LLM_STRUCTURED_OUTPUT_MODE": "json_schema",
    "DINGTALK_APP_KEY": "",
    "DINGTALK_APP_SECRET": "",
    "DINGTALK_CORP_ID": "",
    "JWT_SECRET_KEY": "test-secret-do-not-use-in-production",
    "JWT_ALGORITHM": "HS256",
    "JWT_EXPIRE_HOURS": "8",
    "PII_ENCRYPTION_KEY": "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
    "DAILY_LLM_BUDGET_CNY": "100",
    "MONTHLY_LLM_BUDGET_CNY": "1500",
    "MINERU_MODE": "stub",
    "MINERU_BASE_URL": "",
    "MINERU_API_KEY": "",
    "MINERU_EXPECTED_PROTOCOL_VERSION": "4",
    "MINERU_MODEL_VERSION": "vlm",
    "MINERU_LANGUAGE": "ch",
    "MINERU_UPLOAD_HOSTS": "mineru.oss-cn-shanghai.aliyuncs.com",
    "MINERU_RESULT_HOSTS": "cdn-mineru.openxlab.org.cn",
    "MINERU_POLL_INTERVAL_SECONDS": "0.01",
    "MINERU_TASK_TIMEOUT_SECONDS": "1",
    "MINERU_HTTP_TIMEOUT_SECONDS": "1",
    "MINERU_RESULT_MAX_BYTES": "67108864",
    "MINERU_RESULT_MAX_UNCOMPRESSED_BYTES": "268435456",
    "MINERU_RESULT_MAX_MEMBERS": "512",
    "MINERU_RESULT_MAX_COMPRESSION_RATIO": "100",
    "MAX_RESUME_FILE_BYTES": "20971520",
    "UPLOAD_CHUNK_BYTES": "1048576",
    "MALWARE_SCAN_MODE": "disabled",
    "INGESTION_MAX_ATTEMPTS": "3",
    "INGESTION_LEASE_SECONDS": "900",
    "INGESTION_SWEEP_INTERVAL_SECONDS": "60",
    "INGESTION_BATCH_MAX_FILES": "50",
    "RAW_FILE_PRESIGN_TTL_SECONDS": "300",
    "READ_PAGE_SIZE_DEFAULT": "20",
    "READ_PAGE_SIZE_MAX": "100",
    "GOLDEN_IMPORT_MAX_ROWS": "5000",
    "CORS_ORIGINS": "http://localhost:3000",
}

TEST_INFRA_OVERRIDE_KEYS = frozenset(
    {
        "DATABASE_URL",
        "DATABASE_URL_SYNC",
        "REDIS_URL",
        "MINIO_ENDPOINT",
        "MINIO_ACCESS_KEY",
        "MINIO_SECRET_KEY",
        "MINIO_BUCKET",
        "MINIO_SECURE",
    }
)


def apply_test_environment(environ: MutableMapping[str, str]) -> None:
    external_contract = environ.get("SMARTSCREEN_EXTERNAL_CONTRACT") == "1"
    for key, value in TEST_ENV_DEFAULTS.items():
        if external_contract or key in TEST_INFRA_OVERRIDE_KEYS:
            environ.setdefault(key, value)
        else:
            environ[key] = value
