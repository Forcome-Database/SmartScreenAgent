from __future__ import annotations

from datetime import timedelta
from typing import BinaryIO

from minio import Minio
from minio.error import S3Error

from backend.app.config import get_settings


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
        if not self._client.bucket_exists(self.bucket):
            self._client.make_bucket(self.bucket)

    def put_object(self, key: str, stream: BinaryIO, length: int, *, content_type: str) -> None:
        self._client.put_object(self.bucket, key, stream, length, content_type=content_type)

    def get_object(self, key: str) -> bytes:
        resp = self._client.get_object(self.bucket, key)
        try:
            return resp.read()
        finally:
            resp.close()
            resp.release_conn()

    def presigned_get_url(self, key: str, *, expires_seconds: int = 300) -> str:
        return self._client.presigned_get_object(
            self.bucket, key, expires=timedelta(seconds=expires_seconds)
        )

    def delete_object(self, key: str) -> None:
        try:
            self._client.remove_object(self.bucket, key)
        except S3Error:
            pass
