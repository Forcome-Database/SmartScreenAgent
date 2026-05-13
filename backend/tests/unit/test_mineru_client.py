from pathlib import Path

import pytest
import respx
from httpx import Response

from backend.app.config import get_settings
from backend.app.services.parser.mineru_client import MinerUClient, ParseResult


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
