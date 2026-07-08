
import httpx
import pytest
import respx
from httpx import Response

from backend.app.config import get_settings
from backend.app.services.parser.mineru_client import MinerUClient, MinerUParseError, ParseResult


@pytest.mark.asyncio
async def test_stub_mode_returns_dummy_markdown(monkeypatch, tmp_path):
    monkeypatch.setenv("MINERU_MODE", "stub")
    get_settings.cache_clear()
    pdf = tmp_path / "fake.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    client = MinerUClient()
    result = await client.parse(pdf)
    assert isinstance(result, ParseResult)
    assert result.markdown
    assert result.source == "stub"
    get_settings.cache_clear()


@pytest.mark.asyncio
@respx.mock
async def test_http_mode_posts_to_configured_endpoint(monkeypatch, tmp_path):
    monkeypatch.setenv("MINERU_MODE", "http")
    monkeypatch.setenv("MINERU_BASE_URL", "https://mineru.example.com")
    monkeypatch.setenv("MINERU_API_KEY", "k")
    get_settings.cache_clear()
    pdf = tmp_path / "fake.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    route = respx.post("https://mineru.example.com/file_parse").mock(
        return_value=Response(200, json={"markdown": "# Resume\n张三", "layout": {}})
    )
    client = MinerUClient()
    result = await client.parse(pdf)
    assert route.called
    assert "张三" in result.markdown
    get_settings.cache_clear()


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize(
    ("body", "expected_markdown", "expected_layout"),
    [
        ({"markdown": "# Resume", "layout": {"pages": 1}}, "# Resume", {"pages": 1}),
        ({"markdown": "# Primary", "md_content": "# Alias", "layout": {}}, "# Primary", {}),
        (
            {"data": {"markdown": "# Data", "layout": {"source": "data"}}},
            "# Data",
            {"source": "data"},
        ),
        ({"result": {"markdown": "# Result", "layout": {}}}, "# Result", {}),
        ({"md_content": "# Alias", "layout": {}}, "# Alias", {}),
        ({"data": {"md_content": "# Wrapped Alias", "layout": {}}}, "# Wrapped Alias", {}),
    ],
)
async def test_http_mode_accepts_supported_response_shapes(
    monkeypatch, tmp_path, body, expected_markdown, expected_layout
):
    monkeypatch.setenv("MINERU_MODE", "http")
    monkeypatch.setenv("MINERU_BASE_URL", "https://mineru.example.com")
    get_settings.cache_clear()
    try:
        pdf = tmp_path / "fake.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")
        respx.post("https://mineru.example.com/file_parse").mock(
            return_value=Response(200, json=body)
        )
        result = await MinerUClient().parse(pdf)
        assert result.markdown == expected_markdown
        assert result.layout == expected_layout
        assert result.source == "http"
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({}, "missing markdown"),
        ({"markdown": "   "}, "missing markdown"),
        ({"markdown": "# ok", "layout": []}, "invalid layout"),
    ],
)
async def test_http_mode_rejects_invalid_response(monkeypatch, tmp_path, body, message):
    monkeypatch.setenv("MINERU_MODE", "http")
    monkeypatch.setenv("MINERU_BASE_URL", "https://mineru.example.com")
    get_settings.cache_clear()
    try:
        pdf = tmp_path / "fake.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")
        respx.post("https://mineru.example.com/file_parse").mock(
            return_value=Response(200, json=body)
        )
        with pytest.raises(MinerUParseError, match=message):
            await MinerUClient().parse(pdf)
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize(
    ("response", "message"),
    [
        (Response(200, content=b"not-json"), "invalid json response"),
        (Response(200, json=[]), "invalid json response"),
    ],
)
async def test_http_mode_rejects_unparseable_json(monkeypatch, tmp_path, response, message):
    monkeypatch.setenv("MINERU_MODE", "http")
    monkeypatch.setenv("MINERU_BASE_URL", "https://mineru.example.com")
    get_settings.cache_clear()
    try:
        pdf = tmp_path / "fake.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")
        respx.post("https://mineru.example.com/file_parse").mock(return_value=response)
        with pytest.raises(MinerUParseError) as exc_info:
            await MinerUClient().parse(pdf)
        error = str(exc_info.value)
        assert message in error
        assert "mode=http" in error
        assert "https://mineru.example.com/file_parse" in error
        assert "not-json" not in error
        assert "fake.pdf" not in error
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
@respx.mock
async def test_http_mode_wraps_non_2xx(monkeypatch, tmp_path):
    monkeypatch.setenv("MINERU_MODE", "http")
    monkeypatch.setenv("MINERU_BASE_URL", "https://mineru.example.com")
    get_settings.cache_clear()
    try:
        pdf = tmp_path / "fake.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")
        respx.post("https://mineru.example.com/file_parse").mock(
            return_value=Response(500, json={"error": "boom"})
        )
        with pytest.raises(MinerUParseError) as exc_info:
            await MinerUClient().parse(pdf)
        message = str(exc_info.value)
        assert "mode=http" in message
        assert "https://mineru.example.com/file_parse" in message
        assert "status_code=500" in message
        assert "fake.pdf" not in message
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize(
    ("side_effect", "expected_error"),
    [
        (httpx.ConnectError("connect failed"), "ConnectError"),
        (httpx.TimeoutException("timed out"), "TimeoutException"),
    ],
)
async def test_http_mode_wraps_http_error(monkeypatch, tmp_path, side_effect, expected_error):
    monkeypatch.setenv("MINERU_MODE", "http")
    monkeypatch.setenv("MINERU_BASE_URL", "https://mineru.example.com")
    get_settings.cache_clear()
    try:
        pdf = tmp_path / "fake.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")
        respx.post("https://mineru.example.com/file_parse").mock(side_effect=side_effect)
        with pytest.raises(MinerUParseError) as exc_info:
            await MinerUClient().parse(pdf)
        message = str(exc_info.value)
        assert "mode=http" in message
        assert "https://mineru.example.com/file_parse" in message
        assert expected_error in message
        assert "fake.pdf" not in message
    finally:
        get_settings.cache_clear()
