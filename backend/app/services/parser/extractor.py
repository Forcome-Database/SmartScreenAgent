from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from backend.app.services.llm.gateway import LLMGateway


EXTRACT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": ["string", "null"]},
        "phone": {"type": ["string", "null"]},
        "email": {"type": ["string", "null"]},
        "education": {"type": ["string", "null"]},
        "age": {"type": ["integer", "null"]},
        "experiences": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "company": {"type": "string"},
                    "title": {"type": "string"},
                    "start": {"type": ["string", "null"]},
                    "end": {"type": ["string", "null"]},
                    "description": {"type": "string"},
                },
                "required": ["company", "title", "description"],
            },
        },
    },
    "required": ["name", "experiences"],
}


@dataclass
class Experience:
    company: str
    title: str
    description: str
    start: str | None = None
    end: str | None = None


@dataclass
class ExtractedResume:
    name: str | None
    phone: str | None
    email: str | None
    education: str | None
    age: int | None
    experiences: list[Experience] = field(default_factory=list)
    raw_tokens: int = 0
    model: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ExtractedResume":
        return cls(
            name=d.get("name"),
            phone=d.get("phone"),
            email=d.get("email"),
            education=d.get("education"),
            age=d.get("age"),
            experiences=[
                Experience(
                    company=e["company"],
                    title=e["title"],
                    description=e["description"],
                    start=e.get("start"),
                    end=e.get("end"),
                )
                for e in d.get("experiences", [])
            ],
        )


class ResumeExtractor:
    def __init__(self, gateway: LLMGateway | None = None) -> None:
        self._gateway = gateway or LLMGateway()

    async def extract(self, markdown: str) -> ExtractedResume:
        last_err: Exception | None = None
        for _ in range(2):
            resp = await self._gateway.extract(markdown, schema=EXTRACT_SCHEMA)
            try:
                data = json.loads(resp.content)
                result = ExtractedResume.from_dict(data)
                result.raw_tokens = resp.input_tokens + resp.output_tokens
                result.model = resp.model
                return result
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                last_err = e
        raise ValueError(f"Resume extraction failed: {last_err}")
