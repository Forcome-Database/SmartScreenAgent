from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.app.services.llm.pricing import (
    InvalidPriceBook,
    ModelPriceMissing,
    parse_price_book,
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database
    DATABASE_URL: str
    DATABASE_URL_SYNC: str

    # Redis
    REDIS_URL: str

    # MinIO
    MINIO_ENDPOINT: str
    MINIO_ACCESS_KEY: str
    MINIO_SECRET_KEY: str
    MINIO_BUCKET: str = "resumes"
    MINIO_SECURE: bool = False

    # LLM
    NEWAPI_BASE_URL: str
    NEWAPI_API_KEY: str
    LLM_MODEL_EXTRACT: str
    LLM_MODEL_EXTRACT_FALLBACK: str
    LLM_MODEL_JUDGE: str
    LLM_MODEL_JUDGE_FALLBACK: str
    LLM_MODEL_LIGHT: str
    LLM_STRUCTURED_OUTPUT_MODE: Literal["json_schema", "json_object"] = "json_schema"
    LLM_PRICE_CNY_PER_MILLION_JSON: str
    LLM_BUDGET_WARN_RATIO: float = Field(default=0.80, gt=0, le=1, allow_inf_nan=False)
    LLM_BUDGET_RECONCILE_MAX_PERIODS_PER_RUN: int = Field(default=31, ge=1, le=366)
    LLM_USAGE_PENDING_TIMEOUT_SECONDS: int = Field(default=600, ge=1)
    LLM_USAGE_FINALIZE_MAX_RETRIES: int = Field(default=3, ge=1, le=10)

    # DingTalk
    DINGTALK_APP_KEY: str = ""
    DINGTALK_APP_SECRET: str = ""
    DINGTALK_CORP_ID: str = ""

    # Security
    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_HOURS: int = 8
    PII_ENCRYPTION_KEY: str

    # Cost
    DAILY_LLM_BUDGET_CNY: float = Field(default=100.0, ge=0, allow_inf_nan=False)
    MONTHLY_LLM_BUDGET_CNY: float = Field(default=1500.0, ge=0, allow_inf_nan=False)

    # Quality and cross-engine operations (WP7)
    QUALITY_F1_TARGET: float = Field(default=0.75, ge=0, le=1, allow_inf_nan=False)
    QUALITY_EVIDENCE_COVERAGE_TARGET: float = Field(
        default=0.95, ge=0, le=1, allow_inf_nan=False
    )
    QUALITY_CONFIDENCE_MIN_BUCKET_SIZE: int = Field(default=10, ge=1)
    CROSS_ENGINE_MODEL: str = ""
    CROSS_ENGINE_SAMPLE_PERCENT: int = Field(default=10, ge=0, le=100)
    CROSS_ENGINE_LOW_CONFIDENCE: float = Field(
        default=0.60, ge=0, le=1, allow_inf_nan=False
    )
    CROSS_ENGINE_DIFF_THRESHOLD: float = Field(default=10, ge=0, allow_inf_nan=False)
    CROSS_ENGINE_MAX_ATTEMPTS: int = Field(default=3, ge=1)
    CROSS_ENGINE_LEASE_SECONDS: int = Field(default=900, gt=600)
    CROSS_ENGINE_SWEEP_INTERVAL_SECONDS: int = Field(default=60, ge=1)
    CROSS_ENGINE_BACKFILL_MAX: int = Field(default=500, ge=1)

    # DingTalk recruitment sync (WP8)
    DINGTALK_SYNC_ENABLED: bool = False
    DINGTALK_SYNC_INTERVAL_SECONDS: int = Field(default=1800, ge=1)
    DINGTALK_RECRUITMENT_BASE_URL: str = "https://api.dingtalk.com"
    SYNC_OVERLAP_SECONDS: int = Field(default=300, ge=0)
    SYNC_MAX_ITEMS_PER_RUN: int = Field(default=200, ge=1)
    SYNC_MAX_ITEM_ATTEMPTS: int = Field(default=3, ge=1, le=10)
    SYNC_REPLAY_INTERVAL_SECONDS: int = Field(default=3600, ge=1)

    # MCP surface (WP8)
    MCP_ENABLED: bool = False
    MCP_SERVICE_ROLE: str = "mcp_service"
    MCP_SERVICE_TOKEN: str = ""

    # Resume parser (MinerU)
    MINERU_MODE: Literal["official", "stub"] = "official"
    MINERU_BASE_URL: str = "https://mineru.net"
    MINERU_API_KEY: str = ""
    MINERU_EXPECTED_PROTOCOL_VERSION: int = 4
    MINERU_MODEL_VERSION: Literal["pipeline", "vlm"] = "vlm"
    MINERU_LANGUAGE: str = "ch"
    MINERU_UPLOAD_HOSTS: str = "mineru.oss-cn-shanghai.aliyuncs.com"
    MINERU_RESULT_HOSTS: str = "cdn-mineru.openxlab.org.cn"
    MINERU_POLL_INTERVAL_SECONDS: float = 1.0
    MINERU_TASK_TIMEOUT_SECONDS: float = 3600.0
    MINERU_HTTP_TIMEOUT_SECONDS: float = 120.0
    MINERU_RESULT_MAX_BYTES: int = 64 * 1024 * 1024
    MINERU_RESULT_MAX_UNCOMPRESSED_BYTES: int = 256 * 1024 * 1024
    MINERU_RESULT_MAX_MEMBERS: int = 512
    MINERU_RESULT_MAX_COMPRESSION_RATIO: float = 100.0

    # Resume upload boundary
    MAX_RESUME_FILE_BYTES: int = 20 * 1024 * 1024
    UPLOAD_CHUNK_BYTES: int = 1024 * 1024
    MALWARE_SCAN_MODE: str = "disabled"

    # Ingestion jobs (WP3)
    INGESTION_MAX_ATTEMPTS: int = 3
    # Must stay greater than Celery's `task_time_limit` (see celery_app.py) so
    # a still-running worker's lease never expires — and the sweeper never
    # reclaims a live job — before the task itself would be force-killed.
    INGESTION_LEASE_SECONDS: int = 900
    INGESTION_SWEEP_INTERVAL_SECONDS: int = 60
    INGESTION_BATCH_MAX_FILES: int = 50

    # Read APIs (WP4)
    RAW_FILE_PRESIGN_TTL_SECONDS: int = 300
    READ_PAGE_SIZE_DEFAULT: int = 20
    READ_PAGE_SIZE_MAX: int = 100

    # Golden set (WP6b)
    GOLDEN_IMPORT_MAX_ROWS: int = 5000

    # CORS
    CORS_ORIGINS: str = "http://localhost:3000"

    @model_validator(mode="after")
    def _cross_engine_model_differs_from_primary(self) -> Settings:
        if self.CROSS_ENGINE_MODEL and self.CROSS_ENGINE_MODEL == self.LLM_MODEL_JUDGE:
            raise ValueError("CROSS_ENGINE_MODEL must differ from LLM_MODEL_JUDGE")
        try:
            prices = parse_price_book(self.LLM_PRICE_CNY_PER_MILLION_JSON)
            configured_models = (
                self.LLM_MODEL_EXTRACT,
                self.LLM_MODEL_EXTRACT_FALLBACK,
                self.LLM_MODEL_JUDGE,
                self.LLM_MODEL_JUDGE_FALLBACK,
                self.LLM_MODEL_LIGHT,
            )
            for model in configured_models:
                prices.require(model)
            if self.CROSS_ENGINE_MODEL:
                prices.require(self.CROSS_ENGINE_MODEL)
        except (InvalidPriceBook, ModelPriceMissing) as exc:
            raise ValueError(f"invalid LLM model price configuration: {exc}") from exc
        return self

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def mineru_upload_hosts(self) -> tuple[str, ...]:
        return tuple(
            host.strip().casefold().rstrip(".")
            for host in self.MINERU_UPLOAD_HOSTS.split(",")
            if host.strip()
        )

    @property
    def mineru_result_hosts(self) -> tuple[str, ...]:
        return tuple(
            host.strip().casefold().rstrip(".")
            for host in self.MINERU_RESULT_HOSTS.split(",")
            if host.strip()
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
