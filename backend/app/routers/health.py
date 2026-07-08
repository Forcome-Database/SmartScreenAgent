import inspect

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import get_settings
from backend.app.database import get_db
from backend.app.services.storage.minio_client import MinIOStorage

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz(db: AsyncSession = Depends(get_db)) -> dict:
    settings = get_settings()
    checks: dict[str, str] = {}

    # DB
    try:
        await db.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as e:  # noqa: BLE001
        checks["db"] = f"fail: {e}"

    # Redis
    try:
        r = aioredis.from_url(settings.REDIS_URL)
        try:
            ping_result = r.ping()
            if inspect.isawaitable(ping_result):
                pong = await ping_result
            else:
                pong = ping_result
        finally:
            await r.aclose()
        checks["redis"] = "ok" if pong else "fail"
    except Exception as e:  # noqa: BLE001
        checks["redis"] = f"fail: {e}"

    # MinIO
    try:
        storage = MinIOStorage()
        storage._client.bucket_exists(storage.bucket)  # noqa: SLF001
        checks["minio"] = "ok"
    except Exception as e:  # noqa: BLE001
        checks["minio"] = f"fail: {e}"

    overall = "ok" if all(v == "ok" for v in checks.values()) else "degraded"
    return {"status": overall, "version": "0.1.0", "checks": checks}
