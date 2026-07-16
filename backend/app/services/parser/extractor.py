from __future__ import annotations

import re

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    ValidationError,
    field_validator,
)

from backend.app.services.llm.errors import LLMInvalidOutputError
from backend.app.services.llm.gateway import EXTRACT_PROMPT_VERSION, LLMGateway
from backend.app.services.llm.schemas import LLMResponse
from backend.app.services.llm.structured_output import decode_json_object

_DATE_PATTERN = re.compile(r"^\d{4}(?:-(?:0[1-9]|1[0-2])(?:-(?:0[1-9]|[12]\d|3[01]))?)?$")


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class Experience(_StrictModel):
    company: str = Field(min_length=1, max_length=300)
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(min_length=1, max_length=5000)
    start: str | None = Field(max_length=10)
    end: str | None = Field(max_length=10)

    @field_validator("start", "end")
    @classmethod
    def _canonical_date(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _DATE_PATTERN.fullmatch(value):
            raise ValueError("experience date must be canonical")
        return value


class ExtractedResumePayload(_StrictModel):
    name: str | None = Field(max_length=200)
    phone: str | None = Field(max_length=100)
    email: str | None = Field(max_length=320)
    education: str | None = Field(max_length=300)
    age: StrictInt | None = Field(ge=0, le=120)
    experiences: list[Experience] = Field(max_length=100)

    @field_validator("name", "phone", "email", "education")
    @classmethod
    def _empty_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class ExtractedResume(ExtractedResumePayload):
    raw_tokens: int = Field(default=0, ge=0)
    model: str = ""
    prompt_version: str = EXTRACT_PROMPT_VERSION
    schema_version: int = 1


class ResumeExtractor:
    def __init__(self, gateway: LLMGateway | None = None) -> None:
        self._gateway = gateway or LLMGateway()

    async def extract(self, markdown: str) -> ExtractedResume:
        response = await self._gateway.extract(
            markdown, schema=ExtractedResumePayload.model_json_schema()
        )
        try:
            return self._validate(response)
        except LLMInvalidOutputError as primary_error:
            if response.used_fallback:
                raise
            fallback = await self._gateway.extract(
                markdown,
                schema=ExtractedResumePayload.model_json_schema(),
                fallback_only=True,
            )
            try:
                return self._validate(fallback)
            except LLMInvalidOutputError as fallback_error:
                raise fallback_error from primary_error

    @staticmethod
    def _validate(response: LLMResponse) -> ExtractedResume:
        payload = decode_json_object(response.content)
        try:
            extracted = ExtractedResumePayload.model_validate(payload)
        except ValidationError as exc:
            raise LLMInvalidOutputError("resume extraction output is invalid") from exc
        return ExtractedResume(
            **extracted.model_dump(),
            raw_tokens=response.input_tokens + response.output_tokens,
            model=response.model,
            prompt_version=response.prompt_version or EXTRACT_PROMPT_VERSION,
        )
