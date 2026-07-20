from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Protocol
from uuid import UUID, uuid4

import structlog
from anyio import to_thread

from backend.app.services.storage.minio_client import (
    MinIOStorage,
    ObjectStat,
    StorageError,
)
from backend.app.services.upload.validation import UploadArtifact

logger = structlog.get_logger(__name__)


class StorageClient(Protocol):
    def ensure_bucket(self) -> None: ...

    def put_object(
        self,
        key: str,
        stream: BinaryIO,
        length: int,
        *,
        content_type: str,
        metadata: dict[str, str] | None = None,
    ) -> None: ...

    def stat_object(self, key: str) -> ObjectStat: ...

    def delete_object(self, key: str) -> None: ...

    def download_object(self, key: str, destination: Path) -> None: ...


class StorageIntegrityError(StorageError):
    def __init__(self, *, key: str) -> None:
        super().__init__(operation="verify", key=key)


@dataclass(frozen=True)
class StoredResume:
    object_key: str
    sha256: str
    size_bytes: int
    content_type: str


class ResumeStorageService:
    def __init__(
        self,
        *,
        storage: StorageClient | None = None,
        clock: Callable[[], datetime] | None = None,
        uuid_factory: Callable[[], UUID] | None = None,
    ) -> None:
        self.storage = storage or MinIOStorage()
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.uuid_factory = uuid_factory or uuid4

    async def store(self, artifact: UploadArtifact) -> StoredResume:
        now = self.clock()
        key = f"resumes/{now:%Y/%m}/{self.uuid_factory().hex}"
        stored = StoredResume(
            object_key=key,
            sha256=artifact.sha256,
            size_bytes=artifact.size_bytes,
            content_type=artifact.content_type,
        )
        try:
            await to_thread.run_sync(self.storage.ensure_bucket)
            await to_thread.run_sync(self._put, artifact, stored)
            await self.verify(stored)
        except Exception as exc:
            try:
                await self.delete(key)
            except StorageError as cleanup_exc:
                logger.critical(
                    "raw_file_cleanup_failed",
                    trace_id=structlog.contextvars.get_contextvars().get("trace_id"),
                    object_key=key,
                    sha256=artifact.sha256,
                    error_type=type(cleanup_exc).__name__,
                )
                raise cleanup_exc from exc
            raise
        return stored

    def _put(self, artifact: UploadArtifact, stored: StoredResume) -> None:
        with artifact.path.open("rb") as stream:
            self.storage.put_object(
                stored.object_key,
                stream,
                stored.size_bytes,
                content_type=stored.content_type,
                metadata={"sha256": stored.sha256},
            )

    async def verify(self, stored: StoredResume) -> None:
        stat = await to_thread.run_sync(self.storage.stat_object, stored.object_key)
        checksum = self._metadata_value(stat, "sha256")
        if (
            stat.size != stored.size_bytes
            or stat.content_type != stored.content_type
            or checksum != stored.sha256
        ):
            raise StorageIntegrityError(key=stored.object_key)

    async def delete(self, key: str, *, attempts: int = 3) -> None:
        last_error: StorageError | None = None
        for _ in range(attempts):
            try:
                await to_thread.run_sync(self.storage.delete_object, key)
                return
            except StorageError as exc:
                last_error = exc
        if last_error is not None:
            raise last_error

    async def download_verified(self, stored: StoredResume, destination: Path) -> None:
        try:
            await to_thread.run_sync(
                self.storage.download_object, stored.object_key, destination
            )
            actual_sha256 = await to_thread.run_sync(self._file_sha256, destination)
            if (
                destination.stat().st_size != stored.size_bytes
                or actual_sha256 != stored.sha256
            ):
                raise StorageIntegrityError(key=stored.object_key)
        except Exception:
            destination.unlink(missing_ok=True)
            raise

    @staticmethod
    def _metadata_value(stat: ObjectStat, name: str) -> str | None:
        direct = stat.metadata.get(name)
        if direct is not None:
            return direct
        return stat.metadata.get(f"x-amz-meta-{name}")

    @staticmethod
    def _file_sha256(path: Path) -> str:
        hasher = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                hasher.update(chunk)
        return hasher.hexdigest()
