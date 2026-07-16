import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.app.services.parser.contracts import (
    MinerUHealth,
    MinerUSubmission,
    MinerUTaskStatus,
)

CONTRACTS = Path(__file__).parents[1] / "contracts"
MINERU = CONTRACTS / "mineru" / "3.4.4"


def _load(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def test_source_derived_mineru_fixtures_match_typed_contracts() -> None:
    health = MinerUHealth.model_validate(_load(MINERU / "health.json"))
    submission = MinerUSubmission.model_validate(_load(MINERU / "submission.json"))
    statuses = [
        MinerUTaskStatus.model_validate(_load(path))
        for path in sorted(MINERU.glob("status-*.json"))
    ]

    assert health.version == "3.4.4"
    assert health.protocol_version == 2
    assert submission.task_id == "synthetic-task-001"
    assert {status.status for status in statuses} == {"pending", "completed", "failed"}


def test_malformed_source_fixture_is_rejected() -> None:
    with pytest.raises(ValidationError):
        MinerUHealth.model_validate(_load(MINERU / "malformed-extra-field.json"))


def test_newapi_fixtures_pin_supported_structured_output_modes() -> None:
    strict = _load(CONTRACTS / "newapi" / "strict-json-schema-request.json")
    json_object = _load(CONTRACTS / "newapi" / "json-object-request.json")

    assert strict["response_format"]["json_schema"]["strict"] is True
    assert json_object["response_format"] == {"type": "json_object"}
