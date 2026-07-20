import io

import pytest

from backend.app.services.storage.minio_client import MinIOStorage, StorageError


class FailingMinioClient:
    def put_object(self, *args, **kwargs):
        raise OSError("network details")

    def stat_object(self, *args, **kwargs):
        raise OSError("network details")

    def remove_object(self, *args, **kwargs):
        raise OSError("network details")


@pytest.mark.parametrize("operation", ["put", "stat", "delete"])
def test_minio_sdk_failures_are_typed_and_sanitized(operation):
    storage = MinIOStorage()
    storage._client = FailingMinioClient()
    key = "resumes/opaque"

    with pytest.raises(StorageError) as exc_info:
        if operation == "put":
            storage.put_object(
                key,
                io.BytesIO(b"x"),
                1,
                content_type="application/pdf",
                metadata={"sha256": "a" * 64},
            )
        elif operation == "stat":
            storage.stat_object(key)
        else:
            storage.delete_object(key)

    assert exc_info.value.operation == operation
    assert "network details" not in str(exc_info.value)
