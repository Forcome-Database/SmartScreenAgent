import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.app.services.parser.contracts import (
    MinerUOfficialBatchResponse,
    MinerUOfficialUploadResponse,
)

CONTRACTS = Path(__file__).parents[1] / "contracts"
MINERU = CONTRACTS / "mineru" / "official-v4"


def _load(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def test_source_derived_mineru_fixtures_match_typed_contracts() -> None:
    submission = MinerUOfficialUploadResponse.model_validate(_load(MINERU / "upload-response.json"))
    statuses = [
        MinerUOfficialBatchResponse.model_validate(_load(path))
        for path in sorted(MINERU.glob("status-*.json"))
    ]

    assert submission.data.batch_id == "2bb2f0ec-a336-4a0a-b61a-241afaf9cc87"
    assert {status.data.extract_result[0].state for status in statuses} == {
        "pending",
        "done",
        "failed",
    }


def test_malformed_source_fixture_is_rejected() -> None:
    with pytest.raises(ValidationError):
        MinerUOfficialUploadResponse.model_validate(_load(MINERU / "malformed-extra-field.json"))


def test_newapi_fixtures_pin_supported_structured_output_modes() -> None:
    strict = _load(CONTRACTS / "newapi" / "strict-json-schema-request.json")
    json_object = _load(CONTRACTS / "newapi" / "json-object-request.json")

    assert strict["response_format"]["json_schema"]["strict"] is True
    assert json_object["response_format"] == {"type": "json_object"}
