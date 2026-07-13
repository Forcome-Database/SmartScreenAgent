import io
import socket
from uuid import uuid4

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
        storage.put_object(key, io.BytesIO(b"hello"), 5, content_type="text/plain")
        assert storage.get_object(key) == b"hello"
    finally:
        storage.delete_object(key)


def test_presigned_url(storage: MinIOStorage) -> None:
    key = f"test/presigned-{uuid4().hex}.txt"
    try:
        storage.put_object(key, io.BytesIO(b"hello"), 5, content_type="text/plain")
        assert storage.presigned_get_url(key, expires_seconds=300).startswith("http")
    finally:
        storage.delete_object(key)
