import io
import zipfile
from pathlib import Path

import httpx
import pytest
import respx
from httpx import Response

from backend.app.config import get_settings
from backend.app.services.parser.errors import (
    MinerUContractError,
    MinerUResultError,
    MinerUTaskError,
    MinerUUnavailableError,
)
from backend.app.services.parser.mineru_client import MinerUClient, ParseResult


def _zip_result(markdown: str = "# 张三\n外贸经历") -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("resume/auto/resume.md", markdown.encode())
    return output.getvalue()


def _health(protocol: int = 2) -> dict:
    return {
        "status": "healthy",
        "version": "3.4.4",
        "protocol_version": protocol,
        "queued_tasks": 0,
        "processing_tasks": 0,
        "completed_tasks": 0,
        "failed_tasks": 0,
        "max_concurrent_requests": 2,
    }


def _task(status: str) -> dict:
    return {
        "task_id": "task-123",
        "status": status,
        "backend": "hybrid-engine",
        "file_names": ["resume.pdf"],
        "created_at": "2026-07-16T00:00:00Z",
        "started_at": None,
        "completed_at": None,
        "error": None,
        "status_url": "https://mineru.example/tasks/task-123",
        "result_url": "https://mineru.example/tasks/task-123/result",
        "queued_ahead": 0,
    }


@pytest.fixture(autouse=True)
def _reset_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_stub_mode_returns_dummy_markdown(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MINERU_MODE", "stub")
    pdf = tmp_path / "fake.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")

    result = await MinerUClient().parse(pdf)

    assert isinstance(result, ParseResult)
    assert "张三" in result.markdown
    assert result.source == "stub"


@pytest.mark.asyncio
@respx.mock
async def test_http_mode_uses_protocol_two_task_flow(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MINERU_MODE", "http")
    monkeypatch.setenv("MINERU_BASE_URL", "https://mineru.example")
    monkeypatch.setenv("MINERU_API_KEY", "secret")
    monkeypatch.setenv("MINERU_POLL_INTERVAL_SECONDS", "0")
    pdf = tmp_path / "resume.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")

    respx.get("https://mineru.example/health").mock(return_value=Response(200, json=_health()))
    submit = respx.post("https://mineru.example/tasks").mock(
        return_value=Response(
            202,
            json={
                **_task("pending"),
                "status_url": "https://evil.example/redirect",
                "result_url": "https://evil.example/result",
                "message": "Task submitted successfully",
            },
        )
    )
    status = respx.get("https://mineru.example/tasks/task-123").mock(
        side_effect=[
            Response(200, json=_task("processing")),
            Response(200, json=_task("completed")),
        ]
    )
    result_route = respx.get("https://mineru.example/tasks/task-123/result").mock(
        return_value=Response(
            200,
            content=_zip_result(),
            headers={"content-type": "application/zip"},
        )
    )

    result = await MinerUClient().parse(pdf)

    assert result.markdown.startswith("# 张三")
    assert result.task_id == "task-123"
    assert result.backend == "hybrid-engine"
    assert result.service_version == "3.4.4"
    assert result.protocol_version == 2
    assert result.compressed_bytes > 0
    assert submit.called and status.call_count == 2 and result_route.called
    request = submit.calls[0].request
    assert request.headers["authorization"] == "Bearer secret"
    assert b'name="files"' in request.content
    assert b'name="response_format_zip"' in request.content
    assert b"true" in request.content
    assert not respx.calls[-1].request.url.host == "evil.example"


@pytest.mark.asyncio
@respx.mock
async def test_rejects_protocol_mismatch(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MINERU_MODE", "http")
    monkeypatch.setenv("MINERU_BASE_URL", "https://mineru.example")
    pdf = tmp_path / "resume.pdf"
    pdf.write_bytes(b"x")
    respx.get("https://mineru.example/health").mock(
        return_value=Response(200, json=_health(protocol=3))
    )

    with pytest.raises(MinerUContractError, match="protocol"):
        await MinerUClient().parse(pdf)


@pytest.mark.asyncio
@respx.mock
async def test_wraps_network_failure_as_unavailable(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MINERU_MODE", "http")
    monkeypatch.setenv("MINERU_BASE_URL", "https://mineru.example")
    pdf = tmp_path / "resume.pdf"
    pdf.write_bytes(b"x")
    respx.get("https://mineru.example/health").mock(
        side_effect=httpx.ConnectError("connection failed")
    )

    with pytest.raises(MinerUUnavailableError):
        await MinerUClient().parse(pdf)


@pytest.mark.asyncio
@respx.mock
async def test_terminal_task_failure_is_typed(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MINERU_MODE", "http")
    monkeypatch.setenv("MINERU_BASE_URL", "https://mineru.example")
    monkeypatch.setenv("MINERU_POLL_INTERVAL_SECONDS", "0")
    pdf = tmp_path / "resume.pdf"
    pdf.write_bytes(b"x")
    respx.get("https://mineru.example/health").mock(return_value=Response(200, json=_health()))
    respx.post("https://mineru.example/tasks").mock(
        return_value=Response(202, json={**_task("pending"), "message": "submitted"})
    )
    respx.get("https://mineru.example/tasks/task-123").mock(
        return_value=Response(200, json={**_task("failed"), "error": "private provider text"})
    )

    with pytest.raises(MinerUTaskError) as exc_info:
        await MinerUClient().parse(pdf)
    assert "private provider text" not in str(exc_info.value)


@pytest.mark.asyncio
@respx.mock
async def test_rejects_oversized_result_before_writing(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MINERU_MODE", "http")
    monkeypatch.setenv("MINERU_BASE_URL", "https://mineru.example")
    monkeypatch.setenv("MINERU_POLL_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("MINERU_RESULT_MAX_BYTES", "4")
    pdf = tmp_path / "resume.pdf"
    pdf.write_bytes(b"x")
    respx.get("https://mineru.example/health").mock(return_value=Response(200, json=_health()))
    respx.post("https://mineru.example/tasks").mock(
        return_value=Response(202, json={**_task("pending"), "message": "submitted"})
    )
    respx.get("https://mineru.example/tasks/task-123").mock(
        return_value=Response(200, json=_task("completed"))
    )
    respx.get("https://mineru.example/tasks/task-123/result").mock(
        return_value=Response(
            200,
            content=_zip_result(),
            headers={"content-type": "application/zip", "content-length": "999"},
        )
    )

    with pytest.raises(MinerUResultError, match="compressed size"):
        await MinerUClient().parse(pdf)
