# 注：虽然放在 unit/ 目录，本组测试是 integration 性质（需 MinIO 容器）。
# 用 pytest.mark.integration 标记，CI 可分阶段跑。
import io
import socket

import pytest

from backend.app.config import get_settings
from backend.app.services.storage.minio_client import MinIOStorage

pytestmark = pytest.mark.integration


def _minio_tcp_reachable(endpoint: str, timeout: float = 1.5) -> bool:
    host, port_text = endpoint.rsplit(":", 1)
    try:
        with socket.create_connection((host, int(port_text)), timeout=timeout):
            return True
    except OSError:
        return False


@pytest.fixture
def storage():
    settings = get_settings()
    if not _minio_tcp_reachable(settings.MINIO_ENDPOINT):
        pytest.skip("MinIO not reachable")
    s = MinIOStorage()
    s.ensure_bucket()
    return s


def test_put_and_get(storage):
    key = "test/hello.txt"
    storage.put_object(key, io.BytesIO(b"hello"), 5, content_type="text/plain")
    data = storage.get_object(key)
    assert data == b"hello"


def test_presigned_url(storage):
    key = "test/hello.txt"
    storage.put_object(key, io.BytesIO(b"hello"), 5, content_type="text/plain")
    url = storage.presigned_get_url(key, expires_seconds=300)
    assert url.startswith("http")
