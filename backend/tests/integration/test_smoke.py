"""端到端：起 app → 命中 /healthz → DB / Redis / MinIO 都活着。"""

import io

import pytest
from httpx import ASGITransport, AsyncClient

from backend.app.main import app
from backend.app.services.storage.minio_client import MinIOStorage

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_full_smoke():
    # 1. /healthz 通
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r = await ac.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok", f"healthz returned: {body}"
    assert body["checks"]["db"] == "ok"

    # 2. MinIO 可写读
    storage = MinIOStorage()
    storage.ensure_bucket()
    key = "smoke/test.bin"
    storage.put_object(key, io.BytesIO(b"smoke"), 5, content_type="application/octet-stream")
    assert storage.get_object(key) == b"smoke"


@pytest.mark.skip(reason="requires running celery worker; run manually after `celery worker` is up")
def test_celery_ping_when_worker_up():
    from backend.app.tasks.celery_app import ping

    assert ping.delay().get(timeout=10) == "pong"
