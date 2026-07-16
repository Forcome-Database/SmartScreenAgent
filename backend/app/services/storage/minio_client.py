from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import BinaryIO, cast

from minio import Minio
from minio.error import S3Error
from urllib3.exceptions import HTTPError as Urllib3HTTPError

from backend.app.config import get_settings


class StorageError(RuntimeError):
    def __init__(self, *, operation: str, key: str | None = None) -> None:
        suffix = f" key={key}" if key else ""
        super().__init__(f"Object storage {operation} failed{suffix}")
        self.operation = operation
        self.key = key


class ObjectNotFoundError(StorageError):
    def __init__(self, *, key: str) -> None:
        super().__init__(operation="stat", key=key)


@dataclass(frozen=True)
class ObjectStat:
    key: str
    size: int
    content_type: str | None
    metadata: dict[str, str]


class MinIOStorage:
    def __init__(self) -> None:
        s = get_settings()
        self.bucket = s.MINIO_BUCKET
        self._client = Minio(
            endpoint=s.MINIO_ENDPOINT,
            access_key=s.MINIO_ACCESS_KEY,
            secret_key=s.MINIO_SECRET_KEY,
            secure=s.MINIO_SECURE,
        )

    def ensure_bucket(self) -> None:
        try:
            if not self._client.bucket_exists(self.bucket):
                self._client.make_bucket(self.bucket)
            try:
                policy = self._client.get_bucket_policy(self.bucket)
            except S3Error as exc:
                if exc.code == "NoSuchBucketPolicy":
                    return
                raise
            if policy:
                raise StorageError(operation="bucket_privacy")
        except StorageError:
            raise
        except (S3Error, OSError, Urllib3HTTPError) as exc:
            raise StorageError(operation="ensure_bucket") from exc

    def put_object(
        self,
        key: str,
        stream: BinaryIO,
        length: int,
        *,
        content_type: str,
        metadata: dict[str, str] | None = None,
    ) -> None:
        try:
            self._client.put_object(
                self.bucket,
                key,
                stream,
                length,
                content_type=content_type,
                metadata=cast(
                    dict[str, str | list[str] | tuple[str]] | None,
                    metadata,
                ),
            )
        except (S3Error, OSError, Urllib3HTTPError) as exc:
            raise StorageError(operation="put", key=key) from exc

    def get_object(self, key: str) -> bytes:
        try:
            resp = self._client.get_object(self.bucket, key)
        except (S3Error, OSError, Urllib3HTTPError) as exc:
            raise StorageError(operation="get", key=key) from exc
        try:
            try:
                return resp.read()
            except (OSError, Urllib3HTTPError) as exc:
                raise StorageError(operation="read", key=key) from exc
        finally:
            resp.close()
            resp.release_conn()

    def download_object(self, key: str, destination: Path) -> None:
        try:
            resp = self._client.get_object(self.bucket, key)
        except (S3Error, OSError, Urllib3HTTPError) as exc:
            raise StorageError(operation="get", key=key) from exc
        try:
            with destination.open("wb") as output:
                shutil.copyfileobj(resp, output)
        except (OSError, Urllib3HTTPError) as exc:
            raise StorageError(operation="download", key=key) from exc
        finally:
            resp.close()
            resp.release_conn()

    def stat_object(self, key: str) -> ObjectStat:
        try:
            result = self._client.stat_object(self.bucket, key)
        except S3Error as exc:
            if exc.code in {"NoSuchKey", "NoSuchObject"}:
                raise ObjectNotFoundError(key=key) from exc
            raise StorageError(operation="stat", key=key) from exc
        except (OSError, Urllib3HTTPError) as exc:
            raise StorageError(operation="stat", key=key) from exc
        metadata = {
            str(name).lower(): str(value)
            for name, value in dict(result.metadata or {}).items()
        }
        if result.size is None:
            raise StorageError(operation="stat", key=key)
        return ObjectStat(
            key=key,
            size=result.size,
            content_type=result.content_type,
            metadata=metadata,
        )

    def object_exists(self, key: str) -> bool:
        try:
            self._client.stat_object(self.bucket, key)
        except S3Error as exc:
            if exc.code in {"NoSuchKey", "NoSuchObject"}:
                return False
            raise StorageError(operation="stat", key=key) from exc
        except (OSError, Urllib3HTTPError) as exc:
            raise StorageError(operation="stat", key=key) from exc
        return True

    def list_object_keys(self, *, prefix: str = "") -> list[str]:
        try:
            return [
                item.object_name
                for item in self._client.list_objects(
                    self.bucket,
                    prefix=prefix,
                    recursive=True,
                )
                if item.object_name is not None
            ]
        except (S3Error, OSError, Urllib3HTTPError) as exc:
            raise StorageError(operation="list") from exc

    def presigned_get_url(self, key: str, *, expires_seconds: int = 300) -> str:
        try:
            return self._client.presigned_get_object(
                self.bucket, key, expires=timedelta(seconds=expires_seconds)
            )
        except (S3Error, OSError, Urllib3HTTPError) as exc:
            raise StorageError(operation="presign", key=key) from exc

    def delete_object(self, key: str) -> None:
        try:
            self._client.remove_object(self.bucket, key)
        except (S3Error, OSError, Urllib3HTTPError) as exc:
            raise StorageError(operation="delete", key=key) from exc
