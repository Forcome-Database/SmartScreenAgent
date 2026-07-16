from __future__ import annotations

import asyncio
import json
import mimetypes
import tempfile
from pathlib import Path
from time import monotonic
from typing import Any

import httpx
from pydantic import ValidationError

from backend.app.config import get_settings
from backend.app.services.parser.contracts import (
    MinerUHealth,
    MinerUSubmission,
    MinerUTaskStatus,
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

# Compatibility import for the existing HTTP boundary. New code should catch the
# typed subclasses above when it needs different stable error mappings.
MinerUParseError = MinerUError


class MinerUClient:
    """Protocol-2 client for the self-hosted MinerU task API."""

    def __init__(self) -> None:
        self.settings = get_settings()

    async def parse(self, file_path: Path) -> ParseResult:
        mode = self.settings.MINERU_MODE
        if mode == "stub":
            return self._parse_stub(file_path)
        if mode != "http":
            raise MinerUContractError("unsupported MinerU mode")
        if not self.settings.MINERU_BASE_URL.strip():
            raise MinerUUnavailableError("MinerU base URL is not configured")
        return await self._parse_http(file_path)

    def _parse_stub(self, file_path: Path) -> ParseResult:
        return ParseResult(
            markdown=(
                f"# Stub Resume from {file_path.name}\n\n"
                "姓名：张三\n电话：13800001234\n"
            ),
            backend="stub",
            service_version="stub",
            source="stub",
        )

    @property
    def _base_url(self) -> str:
        return self.settings.MINERU_BASE_URL.rstrip("/")

    @property
    def _headers(self) -> dict[str, str]:
        if not self.settings.MINERU_API_KEY:
            return {}
        return {"Authorization": f"Bearer {self.settings.MINERU_API_KEY}"}

    async def _parse_http(self, file_path: Path) -> ParseResult:
        started = monotonic()
        timeout = httpx.Timeout(self.settings.MINERU_HTTP_TIMEOUT_SECONDS)
        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                follow_redirects=False,
                headers=self._headers,
            ) as client:
                health = await self._fetch_health(client)
                submission = await self._submit(client, file_path)
                status = await self._wait_for_completion(client, submission)
                archive_path, compressed_bytes = await self._download_result(
                    client, submission.task_id
                )
        except asyncio.CancelledError:
            raise
        except MinerUError:
            raise
        except (httpx.TimeoutException, httpx.NetworkError, OSError) as exc:
            raise MinerUUnavailableError("MinerU request failed") from exc

        try:
            archive = read_mineru_result_archive(
                archive_path,
                max_members=self.settings.MINERU_RESULT_MAX_MEMBERS,
                max_uncompressed_bytes=(
                    self.settings.MINERU_RESULT_MAX_UNCOMPRESSED_BYTES
                ),
                max_compression_ratio=(
                    self.settings.MINERU_RESULT_MAX_COMPRESSION_RATIO
                ),
            )
            return ParseResult(
                markdown=archive.markdown,
                content_list=archive.content_list,
                task_id=submission.task_id,
                backend=status.backend,
                service_version=health.version,
                protocol_version=health.protocol_version,
                duration_ms=max(0, round((monotonic() - started) * 1000)),
                compressed_bytes=compressed_bytes,
                uncompressed_bytes=archive.uncompressed_bytes,
                source="http",
            )
        finally:
            archive_path.unlink(missing_ok=True)

    async def _fetch_health(self, client: httpx.AsyncClient) -> MinerUHealth:
        response = await self._request(client, "GET", f"{self._base_url}/health")
        if response.status_code != 200:
            raise MinerUUnavailableError("MinerU health check failed")
        payload = self._json_object(response, "health")
        try:
            health = MinerUHealth.model_validate(payload)
        except ValidationError as exc:
            raise MinerUContractError("invalid MinerU health payload") from exc
        if health.protocol_version != self.settings.MINERU_EXPECTED_PROTOCOL_VERSION:
            raise MinerUContractError("unsupported MinerU protocol version")
        return health

    async def _submit(
        self, client: httpx.AsyncClient, file_path: Path
    ) -> MinerUSubmission:
        mime_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        data: dict[str, str] = {
            "lang_list": self.settings.MINERU_LANGUAGE,
            "backend": self.settings.MINERU_BACKEND,
            "effort": self.settings.MINERU_EFFORT,
            "parse_method": self.settings.MINERU_PARSE_METHOD,
            "formula_enable": "true",
            "table_enable": "true",
            "image_analysis": "false",
            "return_md": "true",
            "return_middle_json": "false",
            "return_model_output": "false",
            "return_content_list": "true",
            "return_images": "false",
            "response_format_zip": "true",
            "return_original_file": "false",
            "client_side_output_generation": "false",
            "start_page_id": "0",
            "end_page_id": "99999",
        }
        try:
            with file_path.open("rb") as handle:
                response = await self._request(
                    client,
                    "POST",
                    f"{self._base_url}/tasks",
                    data=data,
                    files={"files": (file_path.name, handle, mime_type)},
                )
        except OSError as exc:
            raise MinerUTaskError("unable to read resume for MinerU") from exc
        if response.status_code >= 500:
            raise MinerUUnavailableError("MinerU task submission failed")
        if response.status_code != 202:
            raise MinerUTaskError("MinerU rejected parse task")
        payload = self._json_object(response, "submission")
        try:
            return MinerUSubmission.model_validate(payload)
        except ValidationError as exc:
            raise MinerUContractError("invalid MinerU submission payload") from exc

    async def _wait_for_completion(
        self,
        client: httpx.AsyncClient,
        submission: MinerUSubmission,
    ) -> MinerUTaskStatus:
        deadline = monotonic() + self.settings.MINERU_TASK_TIMEOUT_SECONDS
        status_url = f"{self._base_url}/tasks/{submission.task_id}"
        while monotonic() < deadline:
            response = await self._request(client, "GET", status_url)
            if response.status_code >= 500:
                raise MinerUUnavailableError("MinerU status request failed")
            if response.status_code != 200:
                raise MinerUContractError("unexpected MinerU status response")
            payload = self._json_object(response, "status")
            try:
                status = MinerUTaskStatus.model_validate(payload)
            except ValidationError as exc:
                raise MinerUContractError("invalid MinerU status payload") from exc
            if status.task_id != submission.task_id:
                raise MinerUContractError("MinerU status task ID mismatch")
            if status.status == "completed":
                return status
            if status.status == "failed":
                raise MinerUTaskError("MinerU parse task failed")
            await asyncio.sleep(self.settings.MINERU_POLL_INTERVAL_SECONDS)
        raise MinerUUnavailableError("MinerU parse task timed out")

    async def _download_result(
        self, client: httpx.AsyncClient, task_id: str
    ) -> tuple[Path, int]:
        result_url = f"{self._base_url}/tasks/{task_id}/result"
        temporary_path: Path | None = None
        try:
            async with client.stream("GET", result_url) as response:
                if response.status_code >= 500:
                    raise MinerUUnavailableError("MinerU result download failed")
                if response.status_code != 200:
                    raise MinerUTaskError("MinerU result is unavailable")
                content_type = response.headers.get("content-type", "").lower()
                if "application/zip" not in content_type:
                    raise MinerUContractError("MinerU result is not a ZIP archive")
                content_length = response.headers.get("content-length")
                if content_length:
                    try:
                        declared_length = int(content_length)
                    except ValueError as exc:
                        raise MinerUContractError(
                            "invalid MinerU result content length"
                        ) from exc
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
                            raise MinerUResultError(
                                "MinerU result compressed size exceeds limit"
                            )
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
        except (httpx.TimeoutException, httpx.NetworkError, OSError) as exc:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            raise MinerUUnavailableError("MinerU result download failed") from exc

    async def _request(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> httpx.Response:
        try:
            return await client.request(method, url, **kwargs)
        except asyncio.CancelledError:
            raise
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise MinerUUnavailableError("MinerU request failed") from exc

    @staticmethod
    def _json_object(response: httpx.Response, label: str) -> dict[str, Any]:
        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise MinerUContractError(f"invalid MinerU {label} JSON") from exc
        if not isinstance(payload, dict):
            raise MinerUContractError(f"invalid MinerU {label} JSON")
        return payload


__all__ = [
    "MinerUClient",
    "MinerUParseError",
    "ParseResult",
    "MinerUContractError",
    "MinerUResultError",
    "MinerUTaskError",
    "MinerUUnavailableError",
]
