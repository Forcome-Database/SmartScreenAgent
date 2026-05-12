from unittest.mock import AsyncMock
import pytest
from backend.app.services.llm.gateway import LLMGateway, LLMResponse


@pytest.mark.asyncio
async def test_extract_calls_extract_model(monkeypatch):
    gateway = LLMGateway()
    fake = AsyncMock(return_value=LLMResponse(
        content='{"name":"张三"}', model="deepseek-v4", input_tokens=100, output_tokens=20
    ))
    monkeypatch.setattr(gateway, "_call_with_fallback", fake)
    result = await gateway.extract("简历文本", schema={"type": "object"})
    assert result.content == '{"name":"张三"}'
    fake.assert_awaited_once()


@pytest.mark.asyncio
async def test_judge_uses_main_then_fallback():
    """主模型失败时自动切备用。"""
    gateway = LLMGateway()
    call_count = {"n": 0}

    async def fake_call(model: str, prompt: str, **_kw):
        call_count["n"] += 1
        if model == gateway.settings.LLM_MODEL_JUDGE:
            raise RuntimeError("primary down")
        return LLMResponse(content="ok", model=model, input_tokens=10, output_tokens=5)

    gateway._call_once = fake_call  # type: ignore[assignment]
    result = await gateway.judge("prompt", schema={"type": "object"})
    assert call_count["n"] == 2
    assert result.model == gateway.settings.LLM_MODEL_JUDGE_FALLBACK
