from __future__ import annotations

import json
import logging

import httpx
from openai import APIConnectionError, APITimeoutError, AsyncOpenAI, RateLimitError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from backend.app.config import get_settings
from backend.app.services.llm.schemas import LLMResponse

logger = logging.getLogger(__name__)


class LLMGateway:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._client = AsyncOpenAI(
            base_url=self.settings.NEWAPI_BASE_URL,
            api_key=self.settings.NEWAPI_API_KEY,
            timeout=60.0,
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=1, max=10),
        retry=retry_if_exception_type(
            (APIConnectionError, APITimeoutError, RateLimitError, httpx.HTTPError)
        ),
    )
    async def _call_once(
        self,
        model: str,
        prompt: str,
        *,
        response_format: dict | None = None,
        temperature: float = 0.1,
    ) -> LLMResponse:
        kwargs: dict = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
        }
        if response_format:
            kwargs["response_format"] = response_format
        resp = await self._client.chat.completions.create(**kwargs)
        usage = resp.usage
        return LLMResponse(
            content=resp.choices[0].message.content or "",
            model=model,
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
        )

    async def _call_with_fallback(
        self,
        primary: str,
        fallback: str | None,
        prompt: str,
        **kwargs,
    ) -> LLMResponse:
        try:
            return await self._call_once(primary, prompt, **kwargs)
        except Exception as e:
            if not fallback:
                raise
            logger.warning("LLM primary %s failed (%s), falling back to %s", primary, e, fallback)
            return await self._call_once(fallback, prompt, **kwargs)

    async def extract(self, text: str, *, schema: dict) -> LLMResponse:
        prompt = (
            "你是简历结构化抽取助手。基于以下简历内容，输出严格符合 JSON schema 的 JSON。\n\n"
            f"<resume>\n{text}\n</resume>\n\nschema={json.dumps(schema, ensure_ascii=False)}"
        )
        return await self._call_with_fallback(
            self.settings.LLM_MODEL_EXTRACT,
            self.settings.LLM_MODEL_EXTRACT_FALLBACK,
            prompt,
            response_format={"type": "json_object"},
        )

    async def judge(self, prompt: str, *, schema: dict) -> LLMResponse:
        return await self._call_with_fallback(
            self.settings.LLM_MODEL_JUDGE,
            self.settings.LLM_MODEL_JUDGE_FALLBACK,
            prompt,
            response_format={"type": "json_object"},
        )

    async def lightweight(self, prompt: str) -> LLMResponse:
        return await self._call_once(self.settings.LLM_MODEL_LIGHT, prompt)
