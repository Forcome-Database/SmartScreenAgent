from __future__ import annotations

import math
import re
import unicodedata
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from backend.app.rules.schema import JudgeDimension
from backend.app.services.llm.errors import LLMInvalidOutputError
from backend.app.services.llm.gateway import JUDGE_PROMPT_VERSION, LLMGateway
from backend.app.services.llm.schemas import LLMResponse
from backend.app.services.llm.structured_output import decode_json_object


class _JudgeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class JudgeDimensionResult(_JudgeModel):
    id: str = Field(min_length=1, max_length=128)
    tier: str = Field(min_length=1, max_length=64)
    score: int | float | None
    evidence_quotes: list[str] = Field(max_length=10)
    reasoning: str = Field(min_length=1, max_length=4000)
    confidence: int | float = Field(ge=0, le=1)
    suggested_interview_questions: list[str] = Field(max_length=10)

    @field_validator("score", "confidence", mode="before")
    @classmethod
    def _reject_bool_and_non_finite(cls, value: Any) -> Any:
        if isinstance(value, bool):
            raise ValueError("boolean is not a numeric score")
        if isinstance(value, (int, float)) and not math.isfinite(float(value)):
            raise ValueError("numeric value must be finite")
        return value

    @field_validator("evidence_quotes", "suggested_interview_questions")
    @classmethod
    def _non_empty_items(cls, values: list[str]) -> list[str]:
        if any(not value.strip() or len(value) > 1000 for value in values):
            raise ValueError("list item must be non-empty and bounded")
        return values


class JudgeOutput(_JudgeModel):
    dimensions: list[JudgeDimensionResult] = Field(max_length=100)


class JudgeResult(_JudgeModel):
    dimensions: list[JudgeDimensionResult]
    model: str
    tokens: int = Field(ge=0)
    prompt_version: str


def _normalize_evidence(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\s+", " ", normalized).strip().casefold()


class LLMJudge:
    def __init__(self, gateway: LLMGateway | None = None) -> None:
        self._gateway = gateway or LLMGateway()

    async def score(
        self, *, resume_text: str, dims: list[JudgeDimension]
    ) -> JudgeResult:
        if not dims:
            return JudgeResult(dimensions=[], model="", tokens=0, prompt_version="")
        request_payload = {
            "resume_markdown": resume_text,
            "dimensions": [
                {
                    "id": dim.id,
                    "name": dim.name,
                    "prompt_hint": dim.prompt_hint,
                    "tiers": [tier.model_dump() for tier in dim.tiers],
                }
                for dim in dims
            ],
        }
        response = await self._gateway.judge(
            request_payload, schema=JudgeOutput.model_json_schema()
        )
        try:
            return self._validate(response, resume_text=resume_text, dims=dims)
        except LLMInvalidOutputError as primary_error:
            if response.used_fallback:
                raise
            fallback = await self._gateway.judge(
                request_payload,
                schema=JudgeOutput.model_json_schema(),
                fallback_only=True,
            )
            try:
                return self._validate(fallback, resume_text=resume_text, dims=dims)
            except LLMInvalidOutputError as fallback_error:
                raise fallback_error from primary_error

    @staticmethod
    def _validate(
        response: LLMResponse,
        *,
        resume_text: str,
        dims: list[JudgeDimension],
    ) -> JudgeResult:
        payload = decode_json_object(response.content)
        try:
            output = JudgeOutput.model_validate(payload)
        except ValidationError as exc:
            raise LLMInvalidOutputError("judge output is invalid") from exc

        expected = {dim.id: dim for dim in dims}
        actual_ids = [item.id for item in output.dimensions]
        if len(actual_ids) != len(set(actual_ids)) or set(actual_ids) != set(expected):
            raise LLMInvalidOutputError("judge dimension IDs do not match request")

        normalized_source = _normalize_evidence(resume_text)
        ordered: list[JudgeDimensionResult] = []
        by_id = {item.id: item for item in output.dimensions}
        for dim in dims:
            item = by_id[dim.id]
            tier = next(
                (candidate for candidate in dim.tiers if candidate.label == item.tier),
                None,
            )
            if tier is None:
                raise LLMInvalidOutputError("judge tier is not allowed")
            if tier.score is None:
                if item.score is not None or item.evidence_quotes:
                    raise LLMInvalidOutputError("unknown tier must have null score and no evidence")
            else:
                if item.score is None or float(item.score) != float(tier.score):
                    raise LLMInvalidOutputError("judge score does not match tier")
                if not item.evidence_quotes:
                    raise LLMInvalidOutputError("scored judge dimension requires evidence")
                for quote in item.evidence_quotes:
                    normalized_quote = _normalize_evidence(quote)
                    if not normalized_quote or normalized_quote not in normalized_source:
                        raise LLMInvalidOutputError("judge evidence is not present in source")
            ordered.append(item)

        return JudgeResult(
            dimensions=ordered,
            model=response.model,
            tokens=response.input_tokens + response.output_tokens,
            prompt_version=response.prompt_version or JUDGE_PROMPT_VERSION,
        )
