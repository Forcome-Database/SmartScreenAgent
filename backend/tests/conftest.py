import os
from pathlib import Path

from dotenv import load_dotenv

# Load real .env if present (dev/CI on this host); falls through to setdefaults below.
dotenv_path = Path(__file__).resolve().parents[2] / ".env"
if dotenv_path.exists():
    load_dotenv(dotenv_path, override=False)

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
os.environ.setdefault("DATABASE_URL_SYNC", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("MINIO_ENDPOINT", "localhost:9000")
os.environ.setdefault("MINIO_ACCESS_KEY", "test")
os.environ.setdefault("MINIO_SECRET_KEY", "testtest")
os.environ.setdefault("NEWAPI_BASE_URL", "http://localhost/v1")
os.environ.setdefault("NEWAPI_API_KEY", "sk-test")
os.environ.setdefault("LLM_MODEL_EXTRACT", "deepseek-v4")
os.environ.setdefault("LLM_MODEL_EXTRACT_FALLBACK", "gemini-3-flash")
os.environ.setdefault("LLM_MODEL_JUDGE", "gpt-5.5")
os.environ.setdefault("LLM_MODEL_JUDGE_FALLBACK", "gpt-5.4")
os.environ.setdefault("LLM_MODEL_LIGHT", "gemini-3-flash")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-do-not-use-in-prod")
# Fernet keys must be valid base64-encoded 32-byte values; generate one per test run.
from cryptography.fernet import Fernet as _Fernet

os.environ.setdefault("PII_ENCRYPTION_KEY", _Fernet.generate_key().decode())

import pytest


@pytest.fixture(autouse=True)
async def _dispose_db_engine_between_async_tests():
    """Per-test event loop + pooled asyncpg connections don't mix on Windows.

    Each async test gets its own loop; SQLAlchemy's async engine caches
    connections bound to the previous loop, which then raise
    "Event loop is closed" on cleanup. Disposing after each test
    forces fresh connections per loop.
    """
    yield
    try:
        from backend.app.database import engine

        await engine.dispose()
    except Exception:  # noqa: BLE001
        pass
