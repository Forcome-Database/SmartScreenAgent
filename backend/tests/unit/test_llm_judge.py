import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from backend.app.rules.schema import JudgeDimension, Tier
from backend.app.scoring.llm_judge import JudgeResult, LLMJudge
from backend.app.services.llm.errors import LLMInvalidOutputError
from backend.app.services.llm.gateway import LLMGateway
from backend.app.services.llm.schemas import LLMResponse
from backend.app.services.llm.usage import LLMCallContext


def _dim() -> JudgeDimension:
    return JudgeDimension(
        id="independence",
        name="Independent ownership",
        weight=5,
        prompt_hint="Evidence of independent ownership",
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
        name="Communication",
        weight=5,
        prompt_hint="Evidence of customer communication",
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
                "evidence_quotes": ["independently owned US customers"],
                "reasoning": "The resume states independent ownership.",
                "confidence": 0.9,
                "suggested_interview_questions": ["Describe one case."],
            }
        ]
    }


def _context() -> LLMCallContext:
    return LLMCallContext(operation="judge", call_group_id=uuid4())


def _response(
    payload: object,
    *,
    fallback: bool = False,
    call_group_id: UUID | None = None,
) -> LLMResponse:
    return LLMResponse(
        content=json.dumps(payload, ensure_ascii=False),
        model="fallback" if fallback else "primary",
        input_tokens=20,
        output_tokens=8,
        call_group_id=call_group_id or uuid4(),
        prompt_version="resume_judge_v1",
        used_fallback=fallback,
    )


@pytest.mark.asyncio
async def test_judge_returns_validated_source_backed_dimensions() -> None:
    gateway = AsyncMock()
    context = _context()
    gateway.judge.return_value = _response(_payload(), call_group_id=context.call_group_id)

    result = await LLMJudge(gateway=gateway).score(
        resume_text="independently owned US customers",
        dims=[_dim()],
        context=context,
    )

    assert isinstance(result, JudgeResult)
    assert result.dimensions[0].score == 5
    assert result.dimensions[0].tier == "high"
    assert result.model == "primary"
    assert result.tokens == 28
    assert result.call_group_id == context.call_group_id


@pytest.mark.asyncio
async def test_judge_empty_dims_skips_llm_call() -> None:
    gateway = AsyncMock()
    result = await LLMJudge(gateway=gateway).score(
        resume_text="x", dims=[], context=_context()
    )

    assert result == JudgeResult(
        dimensions=[], model="", tokens=0, prompt_version="", call_group_id=None
    )
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
        lambda p: p["dimensions"][0].update(evidence_quotes=["not in source"]),
        lambda p: p["dimensions"][0].update(
            suggested_interview_questions=["question"] * 11
        ),
        lambda p: p["dimensions"][0].update(extra="not allowed"),
    ],
)
async def test_invalid_judge_output_is_rejected(mutator) -> None:
    payload = _payload()
    mutator(payload)
    gateway = AsyncMock()
    context = _context()
    gateway.judge.side_effect = [_response(payload), _response(payload, fallback=True)]

    with pytest.raises(LLMInvalidOutputError):
        await LLMJudge(gateway=gateway).score(
            resume_text="independently owned US customers",
            dims=[_dim()],
            context=context,
        )
    assert gateway.judge.await_count == 2
    assert all(call.kwargs["context"] is context for call in gateway.judge.await_args_list)


@pytest.mark.asyncio
async def test_unknown_tier_requires_null_score_and_no_evidence() -> None:
    payload = _payload()
    payload["dimensions"][0].update(
        tier="unknown", score=None, evidence_quotes=[], confidence=0.2
    )
    gateway = AsyncMock()
    gateway.judge.return_value = _response(payload)

    result = await LLMJudge(gateway=gateway).score(
        resume_text="x", dims=[_dim()], context=_context()
    )

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
        await LLMJudge(gateway=gateway).score(
            resume_text="x", dims=[_dim()], context=_context()
        )


@pytest.mark.asyncio
async def test_unicode_and_whitespace_normalized_evidence_is_accepted() -> None:
    payload = _payload()
    payload["dimensions"][0]["evidence_quotes"] = ["ABC customer"]
    gateway = AsyncMock()
    gateway.judge.return_value = _response(payload)

    result = await LLMJudge(gateway=gateway).score(
        resume_text="ＡＢＣ\n\tcustomer", dims=[_dim()], context=_context()
    )

    assert result.dimensions[0].evidence_quotes == ["ABC customer"]


@pytest.mark.asyncio
async def test_output_is_reordered_to_rule_definition_order() -> None:
    first = _payload()["dimensions"][0]
    second = {
        **deepcopy(first),
        "id": "communication",
        "evidence_quotes": ["customer communication"],
        "reasoning": "The resume states customer communication.",
    }
    payload = {"dimensions": [second, first]}
    gateway = AsyncMock()
    gateway.judge.return_value = _response(payload)

    result = await LLMJudge(gateway=gateway).score(
        resume_text="independently owned US customers; customer communication",
        dims=[_dim(), _second_dim()],
        context=_context(),
    )

    assert [item.id for item in result.dimensions] == ["independence", "communication"]


@pytest.mark.asyncio
async def test_domain_invalid_primary_then_fallback_keeps_paid_attempts_succeeded() -> None:
    invalid = _payload()
    invalid["dimensions"][0]["id"] = "wrong-id"
    recorder = SimpleNamespace(
        begin=AsyncMock(
            side_effect=[
                SimpleNamespace(attempt_id=1, trace_id="trace"),
                SimpleNamespace(attempt_id=2, trace_id="trace"),
            ]
        ),
        finalize=AsyncMock(return_value=True),
    )
    gateway = LLMGateway(recorder=recorder)
    gateway._client.chat.completions.create = AsyncMock(
        side_effect=[
            SimpleNamespace(
                choices=[
                    SimpleNamespace(message=SimpleNamespace(content=json.dumps(invalid)))
                ],
                model="primary",
                usage=SimpleNamespace(prompt_tokens=5, completion_tokens=2),
            ),
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=json.dumps(_payload()))
                    )
                ],
                model="fallback",
                usage=SimpleNamespace(prompt_tokens=5, completion_tokens=2),
            ),
        ]
    )
    context = _context()

    result = await LLMJudge(gateway=gateway).score(
        resume_text="independently owned US customers",
        dims=[_dim()],
        context=context,
    )

    assert result.model == "fallback"
    assert result.call_group_id == context.call_group_id
    assert [call.kwargs["attempt_role"] for call in recorder.begin.await_args_list] == [
        "primary",
        "fallback",
    ]
    assert [call.kwargs["status"] for call in recorder.finalize.await_args_list] == [
        "succeeded",
        "succeeded",
    ]
    assert all(
        call.kwargs["error_code"] is None
        for call in recorder.finalize.await_args_list
    )
