from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


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
    DAILY_LLM_BUDGET_CNY: float = 100.0
    MONTHLY_LLM_BUDGET_CNY: float = 1500.0

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

    # CORS
    CORS_ORIGINS: str = "http://localhost:3000"

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
