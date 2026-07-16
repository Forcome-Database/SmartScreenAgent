from __future__ import annotations

import asyncio
import ipaddress
import json
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from time import monotonic
from typing import Any
from urllib.parse import unquote, urlsplit
from uuid import uuid4

import httpx
from pydantic import ValidationError

from backend.app.config import get_settings
from backend.app.services.parser.contracts import (
    MinerUOfficialBatchResponse,
    MinerUOfficialExtractResult,
    MinerUOfficialUploadResponse,
    ParseResult,
)
from backend.app.services.parser.errors import (
    MinerUContractError,
    MinerUError,
    MinerUResultError,
    MinerUTaskError,
    MinerUUnavailableError,
)
from backend.app.services.parser.result_archive import read_mineru_result_archive

MinerUParseError = MinerUError

_API_AUTH_CODES = {"A0202", "A0211"}
_API_UNAVAILABLE_CODES = {
    -10001,
    -60001,
    -60007,
    -60008,
    -60009,
    -60018,
    -60019,
    -60020,
    -60021,
    -60022,
}
_API_TASK_CODES = {
    -500,
    -10002,
    -60002,
    -60003,
    -60004,
    -60005,
    -60006,
    -60010,
    -60011,
    -60012,
    -60013,
    -60014,
    -60015,
    -60016,
    -60017,
}
_PENDING_STATES = {"waiting-file", "pending", "running", "converting"}


class MinerUClient:
    """Client for the official MinerU API v4 local-file workflow."""

    def __init__(self) -> None:
        self.settings = get_settings()

    async def parse(self, file_path: Path) -> ParseResult:
        mode = self.settings.MINERU_MODE
        if mode == "stub":
            return self._parse_stub(file_path)
        if mode != "official":
            raise MinerUContractError("unsupported MinerU mode")
        if not self.settings.MINERU_API_KEY.strip():
            raise MinerUContractError("MinerU API key is not configured")

        base_url = self._official_base_url()
        started = monotonic()
        timeout = httpx.Timeout(self.settings.MINERU_HTTP_TIMEOUT_SECONDS)
        archive_path: Path | None = None
        try:
            async with (
                httpx.AsyncClient(
                    timeout=timeout,
                    follow_redirects=False,
                    trust_env=False,
                    headers={"Authorization": f"Bearer {self.settings.MINERU_API_KEY}"},
                ) as api_client,
                httpx.AsyncClient(
                    timeout=timeout,
                    follow_redirects=False,
                    trust_env=False,
                ) as blob_client,
            ):
                submission, data_id = await self._request_upload_url(
                    api_client, base_url, file_path
                )
                upload_url = self._validate_asset_url(
                    submission.data.file_urls[0],
                    allowed_hosts=self.settings.mineru_upload_hosts,
                    require_zip=False,
                )
                await self._upload_file(blob_client, upload_url, file_path)
                result = await self._wait_for_completion(
                    api_client,
                    base_url,
                    batch_id=submission.data.batch_id,
                    data_id=data_id,
                    file_name=file_path.name,
                )
                assert result.full_zip_url is not None
                result_url = self._validate_asset_url(
                    result.full_zip_url,
                    allowed_hosts=self.settings.mineru_result_hosts,
                    require_zip=True,
                )
                archive_path, compressed_bytes = await self._download_result(
                    blob_client, result_url
                )

            archive = read_mineru_result_archive(
                archive_path,
                max_members=self.settings.MINERU_RESULT_MAX_MEMBERS,
                max_uncompressed_bytes=(self.settings.MINERU_RESULT_MAX_UNCOMPRESSED_BYTES),
                max_compression_ratio=(self.settings.MINERU_RESULT_MAX_COMPRESSION_RATIO),
            )
            return ParseResult(
                markdown=archive.markdown,
                content_list=archive.content_list,
                task_id=submission.data.batch_id,
                backend=self.settings.MINERU_MODEL_VERSION,
                service_version="official-api-v4",
                protocol_version=self.settings.MINERU_EXPECTED_PROTOCOL_VERSION,
                duration_ms=max(0, round((monotonic() - started) * 1000)),
                compressed_bytes=compressed_bytes,
                uncompressed_bytes=archive.uncompressed_bytes,
                source="official",
            )
        except asyncio.CancelledError:
            raise
        except MinerUError:
            raise
        except (httpx.TransportError, OSError) as exc:
            raise MinerUUnavailableError("MinerU request failed") from exc
        finally:
            if archive_path is not None:
                archive_path.unlink(missing_ok=True)

    def _parse_stub(self, file_path: Path) -> ParseResult:
        return ParseResult(
            markdown=(f"# Stub Resume from {file_path.name}\n\n姓名：张三\n电话：13800001234\n"),
            backend="stub",
            service_version="stub",
            source="stub",
        )

    def _official_base_url(self) -> str:
        raw = self.settings.MINERU_BASE_URL.strip().rstrip("/")
        try:
            parsed = urlsplit(raw)
            port = parsed.port
        except ValueError as exc:
            raise MinerUContractError("invalid MinerU official base URL") from exc
        host = (parsed.hostname or "").casefold().rstrip(".")
        if (
            parsed.scheme != "https"
            or host not in {"mineru.net", "www.mineru.net"}
            or parsed.username is not None
            or parsed.password is not None
            or port not in {None, 443}
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise MinerUContractError("invalid MinerU official base URL")
        return f"https://{host}"

    async def _request_upload_url(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        file_path: Path,
    ) -> tuple[MinerUOfficialUploadResponse, str]:
        data_id = f"smartscreen-{uuid4().hex}"
        response = await self._request_api(
            client,
            "POST",
            f"{base_url}/api/v4/file-urls/batch",
            json={
                "files": [{"name": file_path.name, "data_id": data_id}],
                "model_version": self.settings.MINERU_MODEL_VERSION,
                "language": self.settings.MINERU_LANGUAGE,
                "enable_formula": True,
                "enable_table": True,
            },
        )
        payload = self._success_payload(response, "upload URL response")
        try:
            submission = MinerUOfficialUploadResponse.model_validate(payload)
        except ValidationError as exc:
            raise MinerUContractError("invalid MinerU upload URL response") from exc
        if len(submission.data.file_urls) != 1:
            raise MinerUContractError("unexpected MinerU upload URL count")
        return submission, data_id

    async def _upload_file(
        self,
        client: httpx.AsyncClient,
        upload_url: str,
        file_path: Path,
    ) -> None:
        try:
            size = file_path.stat().st_size
            response = await client.put(
                upload_url,
                content=self._file_chunks(file_path),
                headers={"Content-Length": str(size)},
            )
        except OSError as exc:
            raise MinerUTaskError("unable to read resume for MinerU") from exc
        self._check_blob_response(response, "MinerU file upload")

    async def _wait_for_completion(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        *,
        batch_id: str,
        data_id: str,
        file_name: str,
    ) -> MinerUOfficialExtractResult:
        deadline = monotonic() + self.settings.MINERU_TASK_TIMEOUT_SECONDS
        status_url = f"{base_url}/api/v4/extract-results/batch/{batch_id}"
        while monotonic() < deadline:
            try:
                response = await self._request_api(client, "GET", status_url)
            except MinerUUnavailableError:
                await asyncio.sleep(self.settings.MINERU_POLL_INTERVAL_SECONDS)
                continue
            payload = self._success_payload(response, "batch result response")
            try:
                status = MinerUOfficialBatchResponse.model_validate(payload)
            except ValidationError as exc:
                raise MinerUContractError("invalid MinerU batch result response") from exc
            if status.data.batch_id != batch_id:
                raise MinerUContractError("MinerU batch ID mismatch")

            matches = [
                item
                for item in status.data.extract_result
                if item.data_id == data_id or (item.data_id is None and item.file_name == file_name)
            ]
            if len(matches) != 1:
                raise MinerUContractError("MinerU result identity mismatch")
            item = matches[0]
            if item.state == "done":
                if not item.full_zip_url:
                    raise MinerUContractError("MinerU completed result has no ZIP URL")
                return item
            if item.state == "failed":
                raise MinerUTaskError("MinerU parse task failed")
            if item.state not in _PENDING_STATES:
                raise MinerUContractError("unknown MinerU task state")
            await asyncio.sleep(self.settings.MINERU_POLL_INTERVAL_SECONDS)
        raise MinerUUnavailableError("MinerU parse task timed out")

    async def _download_result(
        self,
        client: httpx.AsyncClient,
        result_url: str,
    ) -> tuple[Path, int]:
        temporary_path: Path | None = None
        try:
            async with client.stream("GET", result_url) as response:
                self._check_blob_response(response, "MinerU result download")
                content_type = response.headers.get("content-type")
                if content_type:
                    media_type = content_type.partition(";")[0].strip().lower()
                    if media_type not in {
                        "application/zip",
                        "application/octet-stream",
                        "binary/octet-stream",
                    }:
                        raise MinerUResultError("invalid MinerU result content type")
                content_length = response.headers.get("content-length")
                if content_length:
                    try:
                        declared_length = int(content_length)
                    except ValueError as exc:
                        raise MinerUContractError("invalid MinerU result content length") from exc
                    if declared_length > self.settings.MINERU_RESULT_MAX_BYTES:
                        raise MinerUResultError("MinerU result compressed size exceeds limit")

                with tempfile.NamedTemporaryFile(
                    prefix="smartscreen-mineru-", suffix=".zip", delete=False
                ) as temporary:
                    temporary_path = Path(temporary.name)
                    total = 0
                    prefix = bytearray()
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > self.settings.MINERU_RESULT_MAX_BYTES:
                            raise MinerUResultError("MinerU result compressed size exceeds limit")
                        if len(prefix) < 4:
                            prefix.extend(chunk[: 4 - len(prefix)])
                        temporary.write(chunk)
                if bytes(prefix) not in {b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"}:
                    raise MinerUResultError("invalid MinerU result ZIP signature")
                return temporary_path, total
        except asyncio.CancelledError:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise
        except MinerUError:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise
        except (httpx.TransportError, OSError) as exc:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise MinerUUnavailableError("MinerU result download failed") from exc

    async def _request_api(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> httpx.Response:
        try:
            response = await client.request(method, url, **kwargs)
        except asyncio.CancelledError:
            raise
        except httpx.TransportError as exc:
            raise MinerUUnavailableError("MinerU request failed") from exc
        if 300 <= response.status_code < 400:
            raise MinerUContractError("MinerU API redirect is not allowed")
        if response.status_code in {408, 425, 429} or response.status_code >= 500:
            raise MinerUUnavailableError("MinerU API is unavailable")
        if response.status_code in {401, 403}:
            raise MinerUContractError("MinerU API authorization was rejected")
        if response.status_code != 200:
            raise MinerUTaskError("MinerU API rejected the request")
        return response

    @staticmethod
    def _check_blob_response(response: httpx.Response, label: str) -> None:
        if 300 <= response.status_code < 400:
            raise MinerUContractError(f"{label} redirect is not allowed")
        if response.status_code in {408, 425, 429} or response.status_code >= 500:
            raise MinerUUnavailableError(f"{label} failed")
        if response.status_code != 200:
            raise MinerUTaskError(f"{label} failed")

    @staticmethod
    def _success_payload(response: httpx.Response, label: str) -> dict[str, Any]:
        payload = MinerUClient._json_object(response, label)
        code = payload.get("code")
        if isinstance(code, bool) or not isinstance(code, (int, str)):
            raise MinerUContractError(f"invalid MinerU {label} code")
        if code == 0:
            return payload
        if code in _API_AUTH_CODES:
            raise MinerUContractError("MinerU API authorization was rejected")
        if code in _API_UNAVAILABLE_CODES:
            raise MinerUUnavailableError("MinerU API is unavailable")
        if code in _API_TASK_CODES:
            raise MinerUTaskError("MinerU API rejected the request")
        raise MinerUContractError("unknown MinerU API error code")

    @staticmethod
    def _json_object(response: httpx.Response, label: str) -> dict[str, Any]:
        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise MinerUContractError(f"invalid MinerU {label} JSON") from exc
        if not isinstance(payload, dict):
            raise MinerUContractError(f"invalid MinerU {label} JSON")
        return payload

    @staticmethod
    def _validate_asset_url(
        value: str,
        *,
        allowed_hosts: tuple[str, ...],
        require_zip: bool,
    ) -> str:
        if not value or len(value) > 4096 or "\\" in value or any(ord(ch) < 32 for ch in value):
            raise MinerUContractError("unsafe MinerU asset URL")
        try:
            parsed = urlsplit(value)
            port = parsed.port
            host = (parsed.hostname or "").encode("ascii").decode().casefold().rstrip(".")
        except (UnicodeError, ValueError) as exc:
            raise MinerUContractError("unsafe MinerU asset URL") from exc
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise MinerUContractError("unsafe MinerU asset URL")
        if (
            parsed.scheme != "https"
            or not host
            or parsed.username is not None
            or parsed.password is not None
            or port not in {None, 443}
            or parsed.fragment
            or not parsed.path
            or host not in allowed_hosts
        ):
            raise MinerUContractError("unsafe MinerU asset URL")
        if require_zip and not unquote(parsed.path).casefold().endswith(".zip"):
            raise MinerUContractError("MinerU result URL is not a ZIP archive")
        return value

    @staticmethod
    async def _file_chunks(file_path: Path) -> AsyncIterator[bytes]:
        with file_path.open("rb") as handle:
            while chunk := await asyncio.to_thread(handle.read, 1024 * 1024):
                yield chunk


__all__ = [
    "MinerUClient",
    "MinerUParseError",
    "ParseResult",
    "MinerUContractError",
    "MinerUResultError",
    "MinerUTaskError",
    "MinerUUnavailableError",
]
