from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

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
    MINERU_MODE: str = "http"  # http | stub
    MINERU_BASE_URL: str = ""
    MINERU_API_KEY: str = ""

    # CORS
    CORS_ORIGINS: str = "http://localhost:3000"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
