from typing import Any

import httpx
import pytest
from pydantic import BaseModel, ConfigDict

from backend.app.config import get_settings
from backend.app.scoring.llm_judge import JudgeDimensionResult
from backend.app.services.llm.gateway import LLMGateway
from backend.app.services.llm.structured_output import decode_json_object
from backend.app.services.parser.extractor import ExtractedResumePayload

pytestmark = pytest.mark.external_contract


class _ProbeJudgeOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    dimensions: list[JudgeDimensionResult]


@pytest.mark.asyncio
async def test_deployed_newapi_lists_every_configured_model() -> None:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.get(
            f"{settings.NEWAPI_BASE_URL.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {settings.NEWAPI_API_KEY}"},
        )
        response.raise_for_status()
    payload: dict[str, Any] = response.json()
    model_ids = {item["id"] for item in payload["data"]}
    assert {
        settings.LLM_MODEL_EXTRACT,
        settings.LLM_MODEL_EXTRACT_FALLBACK,
        settings.LLM_MODEL_JUDGE,
        settings.LLM_MODEL_JUDGE_FALLBACK,
    } <= model_ids


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback_only", [False, True])
async def test_extraction_models_support_configured_structured_output(
    fallback_only: bool,
) -> None:
    gateway = LLMGateway()
    response = await gateway.extract(
        "SYNTHETIC RESUME\nEXPORT EXPERIENCE 2020-2026",
        schema=ExtractedResumePayload.model_json_schema(),
        fallback_only=fallback_only,
    )

    ExtractedResumePayload.model_validate(decode_json_object(response.content))
    assert response.used_fallback is fallback_only


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback_only", [False, True])
async def test_judge_models_support_configured_structured_output(
    fallback_only: bool,
) -> None:
    gateway = LLMGateway()
    response = await gateway.judge(
        {
            "resume_markdown": "SYNTHETIC RESUME\nEXPORT EXPERIENCE 2020-2026",
            "dimensions": [
                {
                    "id": "synthetic_dimension",
                    "name": "Synthetic dimension",
                    "prompt_hint": "Use unknown because no evidence is supplied.",
                    "tiers": [{"label": "unknown", "score": None}],
                }
            ],
        },
        schema=_ProbeJudgeOutput.model_json_schema(),
        fallback_only=fallback_only,
    )

    _ProbeJudgeOutput.model_validate(decode_json_object(response.content))
    assert response.used_fallback is fallback_only
