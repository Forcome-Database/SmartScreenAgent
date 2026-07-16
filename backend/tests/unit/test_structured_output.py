import pytest

from backend.app.services.llm.errors import LLMConfigurationError, LLMInvalidOutputError
from backend.app.services.llm.structured_output import build_response_format, decode_json_object


def test_builds_named_strict_json_schema_format() -> None:
    schema = {"type": "object", "properties": {"name": {"type": "string"}}}

    result = build_response_format(
        schema=schema, schema_name="resume_extract_v1", mode="json_schema"
    )

    assert result == {
        "type": "json_schema",
        "json_schema": {
            "name": "resume_extract_v1",
            "strict": True,
            "schema": schema,
        },
    }


def test_json_object_mode_is_explicit() -> None:
    assert build_response_format(schema={}, schema_name="x", mode="json_object") == {
        "type": "json_object"
    }


def test_rejects_unknown_mode_and_invalid_json_payload() -> None:
    with pytest.raises(LLMConfigurationError):
        build_response_format(schema={}, schema_name="x", mode="automatic")
    with pytest.raises(LLMInvalidOutputError):
        decode_json_object("not-json")
    with pytest.raises(LLMInvalidOutputError):
        decode_json_object("[]")
