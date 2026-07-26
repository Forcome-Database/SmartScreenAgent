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
    "LLM_PRICE_CNY_PER_MILLION_JSON": (
        '{"test-extract":{"input":1.000000,"output":2.000000},'
        '"test-extract-fallback":{"input":1.000000,"output":2.000000},'
        '"test-judge":{"input":1.000000,"output":2.000000},'
        '"test-judge-fallback":{"input":1.000000,"output":2.000000},'
        '"test-light":{"input":1.000000,"output":2.000000},'
        '"test-secondary":{"input":1.000000,"output":2.000000}}'
    ),
    "LLM_BUDGET_WARN_RATIO": "0.80",
    "LLM_BUDGET_RECONCILE_MAX_PERIODS_PER_RUN": "31",
    "LLM_USAGE_PENDING_TIMEOUT_SECONDS": "600",
    "LLM_USAGE_FINALIZE_MAX_RETRIES": "3",
    "DINGTALK_APP_KEY": "",
    "DINGTALK_APP_SECRET": "",
    "DINGTALK_CORP_ID": "",
    "JWT_SECRET_KEY": "test-secret-do-not-use-in-production",
    "JWT_ALGORITHM": "HS256",
    "JWT_EXPIRE_HOURS": "8",
    "PII_ENCRYPTION_KEY": "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
    "DAILY_LLM_BUDGET_CNY": "100",
    "MONTHLY_LLM_BUDGET_CNY": "1500",
    "QUALITY_F1_TARGET": "0.75",
    "QUALITY_EVIDENCE_COVERAGE_TARGET": "0.95",
    "QUALITY_CONFIDENCE_MIN_BUCKET_SIZE": "10",
    "CROSS_ENGINE_MODEL": "test-secondary",
    "CROSS_ENGINE_SAMPLE_PERCENT": "10",
    "CROSS_ENGINE_LOW_CONFIDENCE": "0.60",
    "CROSS_ENGINE_DIFF_THRESHOLD": "10",
    "CROSS_ENGINE_MAX_ATTEMPTS": "3",
    "CROSS_ENGINE_LEASE_SECONDS": "900",
    "CROSS_ENGINE_SWEEP_INTERVAL_SECONDS": "60",
    "CROSS_ENGINE_BACKFILL_MAX": "500",
    "DINGTALK_SYNC_ENABLED": "false",
    "DINGTALK_SYNC_INTERVAL_SECONDS": "1800",
    "DINGTALK_RECRUITMENT_BASE_URL": "https://api.dingtalk.com",
    "SYNC_OVERLAP_SECONDS": "300",
    "SYNC_MAX_ITEMS_PER_RUN": "200",
    "SYNC_MAX_ITEM_ATTEMPTS": "3",
    "SYNC_REPLAY_INTERVAL_SECONDS": "3600",
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

# Test-harness switches that are deliberately NOT `Settings` fields. Consumed
# directly from the environment at import time, before configuration is built.
TEST_ONLY_ENV = {
    # `database.py` drops connection pooling: asyncpg connections are bound to
    # the event loop that opened them, and tests span several loops (one per
    # pytest-asyncio test, plus the in-thread Celery worker's own).
    "SMARTSCREEN_DB_NULLPOOL": "1",
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
    for key, value in TEST_ONLY_ENV.items():
        environ.setdefault(key, value)

    external_contract = environ.get("SMARTSCREEN_EXTERNAL_CONTRACT") == "1"
    for key, value in TEST_ENV_DEFAULTS.items():
        if external_contract or key in TEST_INFRA_OVERRIDE_KEYS:
            environ.setdefault(key, value)
        else:
            environ[key] = value
