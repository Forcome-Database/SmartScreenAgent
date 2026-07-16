import io
import json
import zipfile
from pathlib import Path

import httpx
import pytest
import respx
from httpx import Request, Response

from backend.app.config import get_settings
from backend.app.services.parser.errors import (
    MinerUContractError,
    MinerUResultError,
    MinerUTaskError,
    MinerUUnavailableError,
)
from backend.app.services.parser.mineru_client import MinerUClient, ParseResult

UPLOAD_URL = "https://mineru.oss-cn-shanghai.aliyuncs.com/api-upload/signed?token=x"
RESULT_URL = "https://cdn-mineru.openxlab.org.cn/pdf/result.zip?token=y"
BATCH_ID = "2bb2f0ec-a336-4a0a-b61a-241afaf9cc87"


def _zip_result(markdown: str = "# 张三\n外贸经历") -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("full.md", markdown.encode())
    return output.getvalue()


def _envelope(data: dict, *, code: int | str = 0) -> dict:
    return {
        "code": code,
        "data": data,
        "msg": "ok",
        "trace_id": "trace-123",
    }


def _status(data_id: str, state: str, **extra) -> dict:
    item = {
        "file_name": "resume.pdf",
        "data_id": data_id,
        "state": state,
        "err_msg": "",
        **extra,
    }
    return _envelope({"batch_id": BATCH_ID, "extract_result": [item]})


@pytest.fixture(autouse=True)
def _reset_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _official_settings(monkeypatch) -> None:
    monkeypatch.setenv("MINERU_MODE", "official")
    monkeypatch.setenv("MINERU_BASE_URL", "https://mineru.net")
    monkeypatch.setenv("MINERU_API_KEY", "secret")
    monkeypatch.setenv("MINERU_POLL_INTERVAL_SECONDS", "0")


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
async def test_official_v4_flow_is_typed_and_keeps_token_on_api_origin(
    monkeypatch, tmp_path: Path
) -> None:
    _official_settings(monkeypatch)
    pdf = tmp_path / "resume.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    captured: dict[str, str] = {}

    def submit(request: Request) -> Response:
        payload = json.loads(request.content)
        captured["data_id"] = payload["files"][0]["data_id"]
        assert payload == {
            "files": [{"name": "resume.pdf", "data_id": captured["data_id"]}],
            "model_version": "vlm",
            "language": "ch",
            "enable_formula": True,
            "enable_table": True,
        }
        return Response(
            200,
            json=_envelope({"batch_id": BATCH_ID, "file_urls": [UPLOAD_URL]}),
        )

    poll_count = 0

    def poll(request: Request) -> Response:
        nonlocal poll_count
        states = ["waiting-file", "pending", "running", "converting", "done"]
        state = states[poll_count]
        poll_count += 1
        extra = {"full_zip_url": RESULT_URL} if state == "done" else {}
        return Response(200, json=_status(captured["data_id"], state, **extra))

    submit_route = respx.post("https://mineru.net/api/v4/file-urls/batch").mock(side_effect=submit)
    upload_route = respx.put(UPLOAD_URL).mock(return_value=Response(200))
    poll_route = respx.get(f"https://mineru.net/api/v4/extract-results/batch/{BATCH_ID}").mock(
        side_effect=poll
    )
    result_route = respx.get(RESULT_URL).mock(return_value=Response(200, content=_zip_result()))

    result = await MinerUClient().parse(pdf)

    assert result.markdown.startswith("# 张三")
    assert result.task_id == BATCH_ID
    assert result.backend == "vlm"
    assert result.service_version == "official-api-v4"
    assert result.protocol_version == 4
    assert result.source == "official"
    assert submit_route.called and upload_route.called and poll_route.call_count == 5
    assert result_route.called
    assert submit_route.calls[0].request.headers["authorization"] == "Bearer secret"
    assert poll_route.calls[0].request.headers["authorization"] == "Bearer secret"
    assert "authorization" not in upload_route.calls[0].request.headers
    assert "content-type" not in upload_route.calls[0].request.headers
    assert "authorization" not in result_route.calls[0].request.headers


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize(
    "transport_error",
    [
        httpx.ConnectError("connection failed"),
        httpx.RemoteProtocolError("provider leaked protocol details"),
    ],
)
async def test_wraps_transport_failure_as_unavailable(
    monkeypatch, tmp_path: Path, transport_error: httpx.TransportError
) -> None:
    _official_settings(monkeypatch)
    pdf = tmp_path / "resume.pdf"
    pdf.write_bytes(b"x")
    respx.post("https://mineru.net/api/v4/file-urls/batch").mock(side_effect=transport_error)

    with pytest.raises(MinerUUnavailableError) as exc_info:
        await MinerUClient().parse(pdf)
    assert "protocol details" not in str(exc_info.value)


@pytest.mark.parametrize(
    "url,allowed_hosts,require_zip",
    [
        (
            "http://mineru.oss-cn-shanghai.aliyuncs.com/x",
            ("mineru.oss-cn-shanghai.aliyuncs.com",),
            False,
        ),
        (
            "https://user:secret@mineru.oss-cn-shanghai.aliyuncs.com/x",
            ("mineru.oss-cn-shanghai.aliyuncs.com",),
            False,
        ),
        (
            "https://mineru.oss-cn-shanghai.aliyuncs.com:444/x",
            ("mineru.oss-cn-shanghai.aliyuncs.com",),
            False,
        ),
        (
            "https://mineru.oss-cn-shanghai.aliyuncs.com/x#fragment",
            ("mineru.oss-cn-shanghai.aliyuncs.com",),
            False,
        ),
        ("https://127.0.0.1/x", ("127.0.0.1",), False),
        (
            "https://mineru.oss-cn-shanghai.aliyuncs.com.evil.test/x",
            ("mineru.oss-cn-shanghai.aliyuncs.com",),
            False,
        ),
        ("https://cdn-mineru.openxlab.org.cn/not-zip", ("cdn-mineru.openxlab.org.cn",), True),
    ],
)
def test_rejects_unsafe_asset_urls(
    url: str, allowed_hosts: tuple[str, ...], require_zip: bool
) -> None:
    with pytest.raises(MinerUContractError):
        MinerUClient._validate_asset_url(
            url,
            allowed_hosts=allowed_hosts,
            require_zip=require_zip,
        )


@pytest.mark.asyncio
@respx.mock
async def test_failed_task_is_sanitized(monkeypatch, tmp_path: Path) -> None:
    _official_settings(monkeypatch)
    pdf = tmp_path / "resume.pdf"
    pdf.write_bytes(b"x")
    captured: dict[str, str] = {}

    def submit(request: Request) -> Response:
        captured["data_id"] = json.loads(request.content)["files"][0]["data_id"]
        return Response(
            200,
            json=_envelope({"batch_id": BATCH_ID, "file_urls": [UPLOAD_URL]}),
        )

    respx.post("https://mineru.net/api/v4/file-urls/batch").mock(side_effect=submit)
    respx.put(UPLOAD_URL).mock(return_value=Response(200))
    respx.get(f"https://mineru.net/api/v4/extract-results/batch/{BATCH_ID}").mock(
        side_effect=lambda request: Response(
            200,
            json=_status(
                captured["data_id"],
                "failed",
                err_msg="private provider text and signed URL",
            ),
        )
    )

    with pytest.raises(MinerUTaskError) as exc_info:
        await MinerUClient().parse(pdf)
    assert "private provider text" not in str(exc_info.value)


@pytest.mark.asyncio
@respx.mock
async def test_rejects_mismatched_result_identity(monkeypatch, tmp_path: Path) -> None:
    _official_settings(monkeypatch)
    pdf = tmp_path / "resume.pdf"
    pdf.write_bytes(b"x")
    respx.post("https://mineru.net/api/v4/file-urls/batch").mock(
        return_value=Response(
            200,
            json=_envelope({"batch_id": BATCH_ID, "file_urls": [UPLOAD_URL]}),
        )
    )
    respx.put(UPLOAD_URL).mock(return_value=Response(200))
    respx.get(f"https://mineru.net/api/v4/extract-results/batch/{BATCH_ID}").mock(
        return_value=Response(200, json=_status("wrong-data-id", "pending"))
    )

    with pytest.raises(MinerUContractError, match="identity"):
        await MinerUClient().parse(pdf)


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize(
    "response,expected",
    [
        (Response(429), MinerUUnavailableError),
        (Response(503), MinerUUnavailableError),
        (Response(302, headers={"location": "https://evil.test"}), MinerUContractError),
        (Response(200, json=_envelope({}, code=-60002)), MinerUTaskError),
        (Response(200, json=_envelope({}, code=-10001)), MinerUUnavailableError),
        (Response(200, json=_envelope({}, code="UNKNOWN")), MinerUContractError),
    ],
)
async def test_submission_failures_are_typed(
    monkeypatch, tmp_path: Path, response: Response, expected: type[Exception]
) -> None:
    _official_settings(monkeypatch)
    pdf = tmp_path / "resume.pdf"
    pdf.write_bytes(b"x")
    respx.post("https://mineru.net/api/v4/file-urls/batch").mock(return_value=response)

    with pytest.raises(expected):
        await MinerUClient().parse(pdf)


@pytest.mark.asyncio
@respx.mock
async def test_rejects_oversized_result_before_writing(monkeypatch, tmp_path: Path) -> None:
    _official_settings(monkeypatch)
    monkeypatch.setenv("MINERU_RESULT_MAX_BYTES", "4")
    pdf = tmp_path / "resume.pdf"
    pdf.write_bytes(b"x")
    captured: dict[str, str] = {}

    def submit(request: Request) -> Response:
        captured["data_id"] = json.loads(request.content)["files"][0]["data_id"]
        return Response(
            200,
            json=_envelope({"batch_id": BATCH_ID, "file_urls": [UPLOAD_URL]}),
        )

    respx.post("https://mineru.net/api/v4/file-urls/batch").mock(side_effect=submit)
    respx.put(UPLOAD_URL).mock(return_value=Response(200))
    respx.get(f"https://mineru.net/api/v4/extract-results/batch/{BATCH_ID}").mock(
        side_effect=lambda request: Response(
            200,
            json=_status(captured["data_id"], "done", full_zip_url=RESULT_URL),
        )
    )
    respx.get(RESULT_URL).mock(
        return_value=Response(
            200,
            content=_zip_result(),
            headers={"content-length": "999"},
        )
    )

    with pytest.raises(MinerUResultError, match="compressed size"):
        await MinerUClient().parse(pdf)
