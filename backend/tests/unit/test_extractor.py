import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from backend.app.services.llm.errors import LLMInvalidOutputError
from backend.app.services.llm.schemas import LLMResponse
from backend.app.services.parser.extractor import ExtractedResume, ResumeExtractor

SAMPLE = (Path(__file__).parents[1] / "fixtures" / "sample_resume.md").read_text(encoding="utf-8")


def _response(payload: object, *, fallback: bool = False) -> LLMResponse:
    return LLMResponse(
        content=json.dumps(payload, ensure_ascii=False),
        model="fallback" if fallback else "primary",
        input_tokens=100,
        output_tokens=50,
        prompt_version="resume_extract_v1",
        used_fallback=fallback,
    )


def _raw_response(content: str, *, fallback: bool = False) -> LLMResponse:
    return LLMResponse(
        content=content,
        model="fallback" if fallback else "primary",
        input_tokens=100,
        output_tokens=50,
        prompt_version="resume_extract_v1",
        used_fallback=fallback,
    )


def _valid_payload() -> dict:
    return {
        "name": " 张三 ",
        "phone": "138-0000-1234",
        "email": "zhangsan@example.com",
        "education": "本科",
        "age": 30,
        "experiences": [
            {
                "company": "ABC 外贸公司",
                "title": "外贸业务员",
                "start": "2021-03",
                "end": "2024-05",
                "description": "独立负责美国五金客户开发",
            }
        ],
    }


@pytest.mark.asyncio
async def test_extract_returns_strict_structured_result_with_trusted_metadata() -> None:
    gateway = AsyncMock()
    gateway.extract.return_value = _response(_valid_payload())

    result = await ResumeExtractor(gateway=gateway).extract(SAMPLE)

    assert isinstance(result, ExtractedResume)
    assert result.name == "张三"
    assert result.experiences[0].company == "ABC 外贸公司"
    assert result.raw_tokens == 150
    assert result.model == "primary"
    assert result.prompt_version == "resume_extract_v1"


@pytest.mark.asyncio
async def test_invalid_primary_output_uses_fallback_once() -> None:
    gateway = AsyncMock()
    gateway.extract.side_effect = [
        _response({"name": "张三", "experiences": [], "extra": True}),
        _response(_valid_payload(), fallback=True),
    ]

    result = await ResumeExtractor(gateway=gateway).extract(SAMPLE)

    assert result.model == "fallback"
    assert gateway.extract.await_count == 2
    assert gateway.extract.await_args_list[1].kwargs["fallback_only"] is True


@pytest.mark.asyncio
async def test_optional_empty_strings_normalize_to_none() -> None:
    payload = _valid_payload()
    payload.update(name="   ", phone="\t", email="", education=" \n")
    gateway = AsyncMock()
    gateway.extract.return_value = _response(payload)

    result = await ResumeExtractor(gateway=gateway).extract(SAMPLE)

    assert result.name is None
    assert result.phone is None
    assert result.email is None
    assert result.education is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"age": True},
        {"age": 121},
        {"name": "x" * 201},
        {"experiences": _valid_payload()["experiences"] * 101},
        {"experiences": _valid_payload()["experiences"] * 2},
        {"unexpected": "not allowed"},
        {"experiences": [{"company": "", "title": "x", "description": "x"}]},
        {
            "experiences": [
                {
                    "company": "x",
                    "title": "x",
                    "description": "x",
                    "start": "March 2020",
                    "end": None,
                }
            ]
        },
    ],
)
async def test_invalid_payload_never_returns_result(change: dict) -> None:
    payload = _valid_payload()
    payload.update(change)
    gateway = AsyncMock()
    gateway.extract.side_effect = [_response(payload), _response(payload, fallback=True)]

    with pytest.raises(LLMInvalidOutputError):
        await ResumeExtractor(gateway=gateway).extract(SAMPLE)
    assert gateway.extract.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [[], "text", 1, None])
async def test_wrong_top_level_type_is_rejected_after_two_attempts(payload: object) -> None:
    gateway = AsyncMock()
    gateway.extract.side_effect = [_response(payload), _response(payload, fallback=True)]

    with pytest.raises(LLMInvalidOutputError):
        await ResumeExtractor(gateway=gateway).extract(SAMPLE)

    assert gateway.extract.await_count == 2


@pytest.mark.asyncio
async def test_invalid_json_is_rejected_after_two_attempts() -> None:
    gateway = AsyncMock()
    gateway.extract.side_effect = [
        _raw_response("{not-json"),
        _raw_response("{not-json", fallback=True),
    ]

    with pytest.raises(LLMInvalidOutputError):
        await ResumeExtractor(gateway=gateway).extract(SAMPLE)

    assert gateway.extract.await_count == 2


@pytest.mark.asyncio
async def test_missing_required_key_is_rejected_after_two_attempts() -> None:
    payload = _valid_payload()
    del payload["email"]
    gateway = AsyncMock()
    gateway.extract.side_effect = [_response(payload), _response(payload, fallback=True)]

    with pytest.raises(LLMInvalidOutputError):
        await ResumeExtractor(gateway=gateway).extract(SAMPLE)

    assert gateway.extract.await_count == 2
