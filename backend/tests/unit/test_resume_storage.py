import hashlib
import io
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest

from backend.app.services.storage.minio_client import ObjectStat, StorageError
from backend.app.services.storage.resume_storage import (
    ResumeStorageService,
    StorageIntegrityError,
)
from backend.app.services.upload.validation import UploadArtifact


class FakeStorage:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str, dict[str, str]]] = {}
        self.deleted: list[str] = []
        self.stat_size_delta = 0
        self.delete_failures = 0

    def ensure_bucket(self) -> None:
        return None

    def put_object(
        self,
        key: str,
        stream: io.BufferedReader,
        length: int,
        *,
        content_type: str,
        metadata: dict[str, str] | None = None,
    ) -> None:
        body = stream.read()
        assert len(body) == length
        self.objects[key] = (body, content_type, metadata or {})

    def stat_object(self, key: str) -> ObjectStat:
        body, content_type, metadata = self.objects[key]
        return ObjectStat(
            key=key,
            size=len(body) + self.stat_size_delta,
            content_type=content_type,
            metadata={f"x-amz-meta-{k}": v for k, v in metadata.items()},
        )

    def delete_object(self, key: str) -> None:
        if self.delete_failures:
            self.delete_failures -= 1
            raise StorageError(operation="delete", key=key)
        self.deleted.append(key)
        self.objects.pop(key, None)

    def download_object(self, key: str, destination: Path) -> None:
        destination.write_bytes(self.objects[key][0])


def _artifact(path: Path, body: bytes, name: str = "张三-resume.pdf") -> UploadArtifact:
    path.write_bytes(body)
    return UploadArtifact(
        path=path,
        original_filename=name,
        content_type="application/pdf",
        size_bytes=len(body),
        sha256=hashlib.sha256(body).hexdigest(),
    )


@pytest.mark.asyncio
async def test_store_uses_opaque_key_and_verifies_metadata(tmp_path):
    storage = FakeStorage()
    artifact = _artifact(tmp_path / "resume.pdf", b"resume")
    service = ResumeStorageService(
        storage=storage,
        clock=lambda: datetime(2026, 7, 16, tzinfo=timezone.utc),
        uuid_factory=lambda: UUID("01234567-89ab-cdef-0123-456789abcdef"),
    )

    stored = await service.store(artifact)

    assert stored.object_key == "resumes/2026/07/0123456789abcdef0123456789abcdef"
    assert "张三" not in stored.object_key
    assert "resume.pdf" not in stored.object_key
    assert stored.sha256 == artifact.sha256
    assert stored.size_bytes == artifact.size_bytes
    body, content_type, metadata = storage.objects[stored.object_key]
    assert body == b"resume"
    assert content_type == "application/pdf"
    assert metadata == {"sha256": artifact.sha256}


@pytest.mark.asyncio
async def test_store_deletes_object_when_stat_verification_fails(tmp_path):
    storage = FakeStorage()
    storage.stat_size_delta = 1
    artifact = _artifact(tmp_path / "resume.pdf", b"resume")
    service = ResumeStorageService(storage=storage)

    with pytest.raises(StorageIntegrityError):
        await service.store(artifact)

    assert len(storage.deleted) == 1
    assert storage.objects == {}


@pytest.mark.asyncio
async def test_cleanup_retries_typed_storage_failure(tmp_path):
    storage = FakeStorage()
    artifact = _artifact(tmp_path / "resume.pdf", b"resume")
    service = ResumeStorageService(storage=storage)
    stored = await service.store(artifact)
    storage.delete_failures = 2

    await service.delete(stored.object_key)

    assert storage.deleted == [stored.object_key]
    assert storage.objects == {}

    await service.delete(stored.object_key)
    assert storage.deleted == [stored.object_key, stored.object_key]


@pytest.mark.asyncio
async def test_cleanup_failure_is_logged_without_filename(tmp_path, monkeypatch):
    from unittest.mock import Mock

    from backend.app.services.storage import resume_storage as module

    storage = FakeStorage()
    storage.stat_size_delta = 1
    storage.delete_failures = 3
    artifact = _artifact(tmp_path / "resume.pdf", b"resume", name="secret-name.pdf")
    critical = Mock()
    monkeypatch.setattr(module.logger, "critical", critical)

    with pytest.raises(StorageError):
        await ResumeStorageService(storage=storage).store(artifact)

    critical.assert_called_once()
    _, kwargs = critical.call_args
    assert kwargs["sha256"] == artifact.sha256
    assert "trace_id" in kwargs
    assert "secret-name.pdf" not in str(critical.call_args)
