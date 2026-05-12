# 注：虽然放在 unit/ 目录，本组测试是 integration 性质（需 MinIO 容器）。
# 用 pytest.mark.integration 标记，CI 可分阶段跑。
import io
import pytest
from backend.app.services.storage.minio_client import MinIOStorage

pytestmark = pytest.mark.integration


@pytest.fixture
def storage():
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
