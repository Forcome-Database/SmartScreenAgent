from __future__ import annotations

import json
from typing import Any

from backend.app.services.llm.errors import LLMConfigurationError, LLMInvalidOutputError


def build_response_format(
    *, schema: dict[str, Any], schema_name: str, mode: str
) -> dict[str, Any]:
    if mode == "json_schema":
        return {
            "type": "json_schema",
            "json_schema": {
                "name": schema_name,
                "strict": True,
                "schema": schema,
            },
        }
    if mode == "json_object":
        return {"type": "json_object"}
    raise LLMConfigurationError("unsupported structured output mode")


def decode_json_object(content: str) -> dict[str, Any]:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise LLMInvalidOutputError("model output is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise LLMInvalidOutputError("model output must be a JSON object")
    return payload
