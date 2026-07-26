from __future__ import annotations

import pytest
from sqlalchemy.exc import SQLAlchemyError

from backend.app.services.cross_check.worker import SourceMissing, _classify, _sanitize
from backend.app.services.llm.errors import (
    LLMConfigurationError,
    LLMInvalidOutputError,
    LLMInvalidResponseError,
    LLMUnavailableError,
    ModelPriceMissing,
    UsageLedgerUnavailable,
)


class _Dimension:
    def __init__(self) -> None:
        self.id = "independence"
        self.tier = "high"
        self.score = 10
        self.confidence = 0.9
        self.evidence_quotes = ["a quotable line from the resume"]
        self.reasoning = "private secondary reasoning"
        self.suggested_interview_questions = ["a question"]


def test_only_comparable_fields_survive_sanitization() -> None:
    (sanitized,) = _sanitize([_Dimension()])

    assert set(sanitized) == {"id", "tier", "score", "confidence"}
    serialized = str(sanitized)
    # A second copy of candidate-derived text would double the leak surface.
    assert "quotable" not in serialized
    assert "reasoning" not in serialized
    assert "question" not in serialized


@pytest.mark.parametrize(
    ("exc", "code", "retryable"),
    [
        (ModelPriceMissing("m"), "model_price_missing", False),
        (UsageLedgerUnavailable("x"), "usage_ledger_unavailable", True),
        (LLMConfigurationError("x"), "provider_configuration_error", False),
        (LLMInvalidResponseError("x"), "invalid_secondary_output", False),
        (LLMInvalidOutputError("x"), "invalid_secondary_output", False),
        (LLMUnavailableError("x"), "provider_unavailable", True),
        (SourceMissing("x"), "source_missing", False),
        (SQLAlchemyError("x"), "database_unavailable", True),
        (OSError("x"), "database_unavailable", True),
        (RuntimeError("x"), "cross_check_unexpected", True),
    ],
)
def test_failures_map_to_stable_codes(
    exc: BaseException, code: str, retryable: bool
) -> None:
    assert _classify(exc) == (code, retryable)


def test_price_and_ledger_are_classified_before_the_generic_configuration_case() -> None:
    # ModelPriceMissing subclasses LLMConfigurationError, so ordering decides
    # whether a missing price is (correctly) terminal for its own reason.
    assert isinstance(ModelPriceMissing("m"), LLMConfigurationError)
    assert _classify(ModelPriceMissing("m"))[0] == "model_price_missing"
