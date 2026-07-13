"""End-to-end smoke tests for the app and its external services."""

import io
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from backend.app.main import app
from backend.app.services.storage.minio_client import MinIOStorage

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_full_smoke() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok", f"healthz returned: {body}"
    assert body["checks"]["db"] == "ok"

    storage = MinIOStorage()
    storage.ensure_bucket()
    key = f"smoke/test-{uuid4().hex}.bin"
    try:
        storage.put_object(
            key,
            io.BytesIO(b"smoke"),
            5,
            content_type="application/octet-stream",
        )
        assert storage.get_object(key) == b"smoke"
    finally:
        storage.delete_object(key)


def test_celery_ping_when_worker_up(celery_worker) -> None:
    from backend.app.tasks.celery_app import ping

    result = ping.delay()
    try:
        assert result.get(timeout=10) == "pong"
    finally:
        result.forget()
