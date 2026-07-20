import json
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from backend.app.rules.schema import JudgeDimension, Tier
from backend.app.scoring.llm_judge import JudgeResult, LLMJudge
from backend.app.services.llm.errors import LLMInvalidOutputError
from backend.app.services.llm.schemas import LLMResponse


def _dim() -> JudgeDimension:
    return JudgeDimension(
        id="independence",
        name="独立处理事务",
        weight=5,
        prompt_hint="证据：独立负责",
        tiers=[
            Tier(label="high", score=5),
            Tier(label="mid", score=2),
            Tier(label="low", score=0),
            Tier(label="unknown", score=None),
        ],
    )


def _second_dim() -> JudgeDimension:
    return JudgeDimension(
        id="communication",
        name="沟通能力",
        weight=5,
        prompt_hint="证据：客户沟通",
        tiers=[
            Tier(label="high", score=5),
            Tier(label="mid", score=2),
            Tier(label="low", score=0),
            Tier(label="unknown", score=None),
        ],
    )


def _payload() -> dict:
    return {
        "dimensions": [
            {
                "id": "independence",
                "tier": "high",
                "score": 5,
                "evidence_quotes": ["独立负责 美国客户"],
                "reasoning": "简历明确说明独立负责",
                "confidence": 0.9,
                "suggested_interview_questions": ["请介绍一个案例"],
            }
        ]
    }


def _response(payload: object, *, fallback: bool = False) -> LLMResponse:
    return LLMResponse(
        content=json.dumps(payload, ensure_ascii=False),
        model="fallback" if fallback else "primary",
        input_tokens=20,
        output_tokens=8,
        prompt_version="resume_judge_v1",
        used_fallback=fallback,
    )


@pytest.mark.asyncio
async def test_judge_returns_validated_source_backed_dimensions() -> None:
    gateway = AsyncMock()
    gateway.judge.return_value = _response(_payload())

    result = await LLMJudge(gateway=gateway).score(
        resume_text="独立负责\n美国客户开发", dims=[_dim()]
    )

    assert isinstance(result, JudgeResult)
    assert result.dimensions[0].score == 5
    assert result.dimensions[0].tier == "high"
    assert result.model == "primary"
    assert result.tokens == 28


@pytest.mark.asyncio
async def test_judge_empty_dims_skips_llm_call() -> None:
    gateway = AsyncMock()
    result = await LLMJudge(gateway=gateway).score(resume_text="x", dims=[])

    assert result == JudgeResult(dimensions=[], model="", tokens=0, prompt_version="")
    gateway.judge.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutator",
    [
        lambda p: p["dimensions"][0].update(id="unknown"),
        lambda p: p["dimensions"].append(deepcopy(p["dimensions"][0])),
        lambda p: p.update(dimensions=[]),
        lambda p: p["dimensions"][0].update(tier="invented"),
        lambda p: p["dimensions"][0].update(score=2),
        lambda p: p["dimensions"][0].update(score=True),
        lambda p: p["dimensions"][0].update(score=float("nan")),
        lambda p: p["dimensions"][0].update(score=float("inf")),
        lambda p: p["dimensions"][0].update(confidence=1.5),
        lambda p: p["dimensions"][0].update(confidence=float("nan")),
        lambda p: p["dimensions"][0].update(reasoning=" "),
        lambda p: p["dimensions"][0].update(evidence_quotes=[]),
        lambda p: p["dimensions"][0].update(evidence_quotes=["不存在的证据"]),
        lambda p: p["dimensions"][0].update(suggested_interview_questions=["问题"] * 11),
        lambda p: p["dimensions"][0].update(extra="not allowed"),
    ],
)
async def test_invalid_judge_output_is_rejected(mutator) -> None:
    payload = _payload()
    mutator(payload)
    gateway = AsyncMock()
    gateway.judge.side_effect = [_response(payload), _response(payload, fallback=True)]

    with pytest.raises(LLMInvalidOutputError):
        await LLMJudge(gateway=gateway).score(resume_text="独立负责 美国客户开发", dims=[_dim()])
    assert gateway.judge.await_count == 2


@pytest.mark.asyncio
async def test_unknown_tier_requires_null_score_and_no_evidence() -> None:
    payload = _payload()
    payload["dimensions"][0].update(tier="unknown", score=None, evidence_quotes=[], confidence=0.2)
    gateway = AsyncMock()
    gateway.judge.return_value = _response(payload)

    result = await LLMJudge(gateway=gateway).score(resume_text="x", dims=[_dim()])

    assert result.dimensions[0].score is None


@pytest.mark.asyncio
async def test_unknown_tier_rejects_evidence() -> None:
    payload = _payload()
    payload["dimensions"][0].update(
        tier="unknown", score=None, evidence_quotes=["x"], confidence=0.2
    )
    gateway = AsyncMock()
    gateway.judge.side_effect = [_response(payload), _response(payload, fallback=True)]

    with pytest.raises(LLMInvalidOutputError, match="unknown tier"):
        await LLMJudge(gateway=gateway).score(resume_text="x", dims=[_dim()])


@pytest.mark.asyncio
async def test_unicode_and_whitespace_normalized_evidence_is_accepted() -> None:
    payload = _payload()
    payload["dimensions"][0]["evidence_quotes"] = ["ABC 客户"]
    gateway = AsyncMock()
    gateway.judge.return_value = _response(payload)

    result = await LLMJudge(gateway=gateway).score(resume_text="ＡＢＣ\n\t客户", dims=[_dim()])

    assert result.dimensions[0].evidence_quotes == ["ABC 客户"]


@pytest.mark.asyncio
async def test_output_is_reordered_to_rule_definition_order() -> None:
    first = _payload()["dimensions"][0]
    second = {
        **deepcopy(first),
        "id": "communication",
        "evidence_quotes": ["客户沟通"],
        "reasoning": "简历明确提到客户沟通",
    }
    payload = {"dimensions": [second, first]}
    gateway = AsyncMock()
    gateway.judge.return_value = _response(payload)

    result = await LLMJudge(gateway=gateway).score(
        resume_text="独立负责 美国客户开发；客户沟通", dims=[_dim(), _second_dim()]
    )

    assert [item.id for item in result.dimensions] == ["independence", "communication"]
