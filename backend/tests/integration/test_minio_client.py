import io
import socket
from uuid import uuid4

import httpx
import pytest

from backend.app.config import get_settings
from backend.app.services.storage.minio_client import MinIOStorage
from backend.tests.integration.runtime import require_service

pytestmark = pytest.mark.integration


def _minio_reachable(endpoint: str, timeout: float = 1.5) -> bool:
    host, port_text = endpoint.rsplit(":", 1)
    try:
        with socket.create_connection((host, int(port_text)), timeout=timeout):
            return True
    except OSError:
        return False


@pytest.fixture
def storage() -> MinIOStorage:
    settings = get_settings()
    require_service("MinIO", reachable=_minio_reachable(settings.MINIO_ENDPOINT))
    result = MinIOStorage()
    result.ensure_bucket()
    return result


def test_put_and_get(storage: MinIOStorage) -> None:
    key = f"test/hello-{uuid4().hex}.txt"
    try:
        storage.put_object(
            key,
            io.BytesIO(b"hello"),
            5,
            content_type="text/plain",
            metadata={"sha256": "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"},
        )
        assert storage.get_object(key) == b"hello"
        stat = storage.stat_object(key)
        assert stat.size == 5
        assert stat.content_type == "text/plain"
        assert stat.metadata["x-amz-meta-sha256"].startswith("2cf24dba")
        assert storage.object_exists(key)
    finally:
        storage.delete_object(key)
    assert not storage.object_exists(key)


def test_presigned_url(storage: MinIOStorage) -> None:
    key = f"test/presigned-{uuid4().hex}.txt"
    try:
        storage.put_object(key, io.BytesIO(b"hello"), 5, content_type="text/plain")
        presigned = storage.presigned_get_url(key, expires_seconds=300)
        assert presigned.startswith("http")
        with httpx.Client(trust_env=False, follow_redirects=True) as client:
            presigned_response = client.get(presigned)
            assert presigned_response.status_code == 200, presigned_response.text
            assert presigned_response.content == b"hello"

            settings = get_settings()
            scheme = "https" if settings.MINIO_SECURE else "http"
            anonymous = f"{scheme}://{settings.MINIO_ENDPOINT}/{storage.bucket}/{key}"
            assert client.get(anonymous).status_code == 403
    finally:
        storage.delete_object(key)
