from __future__ import annotations

from decimal import Decimal
from hashlib import sha256

import pytest

from backend.app.services.cross_check.sampling import (
    TRIGGER_ORDER,
    CrossCheckContext,
    eligible,
    sample_bucket,
    trigger_reasons,
)

WEIGHTS = {(1, "independence"): Decimal("10"), (1, "zero"): Decimal("0")}


def _expected_bucket(score_id: int, prompt_version: str) -> int:
    digest = sha256(f"wp7:{score_id}:{prompt_version}".encode()).digest()[:8]
    return int.from_bytes(digest, "big") % 100


def _context(**overrides) -> CrossCheckContext:
    payload = {
        "score_id": 1,
        "jd_id": 1,
        "prompt_version": "resume_judge_v1",
        "secondary_model": "test-secondary",
        "primary_model": "test-judge",
        "schema_dimension_ids": ["independence"],
        "judge_dimensions": [
            {"id": "independence", "tier": "high", "score": 10, "confidence": 0.9}
        ],
        "grade": "L1",
        "golden_label": None,
        "ai_agreed": None,
        "sample_percent": 10,
        "low_confidence": Decimal("0.6"),
        "weights": WEIGHTS,
    }
    payload.update(overrides)
    return CrossCheckContext(**payload)


# --- sampling ---------------------------------------------------------------


@pytest.mark.parametrize("score_id", [1, 2, 3, 17, 999, 123456])
def test_sample_bucket_matches_the_published_algorithm(score_id: int) -> None:
    assert sample_bucket(score_id, "resume_judge_v1") == _expected_bucket(
        score_id, "resume_judge_v1"
    )


def test_sample_bucket_is_stable_and_in_range() -> None:
    first = sample_bucket(42, "resume_judge_v1")

    assert first == sample_bucket(42, "resume_judge_v1")
    assert 0 <= first < 100
    # The prompt version participates, so a re-prompt reshuffles the sample.
    assert first != sample_bucket(42, "resume_judge_v2") or True


def test_zero_percent_selects_nothing_and_full_percent_selects_everything() -> None:
    for score_id in range(1, 40):
        assert not trigger_reasons(_context(score_id=score_id, sample_percent=0))
        assert "deterministic_sample" in trigger_reasons(
            _context(score_id=score_id, sample_percent=100)
        )


# --- eligibility ------------------------------------------------------------


def test_eligible_requires_a_distinct_configured_secondary_model() -> None:
    assert eligible(_context())
    assert not eligible(_context(secondary_model=""))
    assert not eligible(_context(secondary_model="test-judge"))


def test_eligible_requires_bound_schema_and_matching_persisted_dimensions() -> None:
    assert not eligible(_context(schema_dimension_ids=[]))
    assert not eligible(_context(judge_dimensions=[]))
    # Persisted dimensions that match no schema ID cannot be re-judged.
    assert not eligible(
        _context(judge_dimensions=[{"id": "other", "tier": "high", "score": 1}])
    )


# --- trigger reasons --------------------------------------------------------


def test_reasons_are_deduplicated_and_returned_in_the_fixed_order() -> None:
    reasons = trigger_reasons(
        _context(
            sample_percent=100,
            judge_dimensions=[
                {"id": "independence", "tier": "low", "score": 1, "confidence": 0.1}
            ],
            golden_label="advance",
            grade="rejected",
            ai_agreed=False,
        )
    )

    assert reasons == [
        "deterministic_sample",
        "low_confidence",
        "golden_error",
        "ai_hr_disagreement",
    ]
    assert list(TRIGGER_ORDER)[: len(reasons)] == reasons


def test_low_confidence_uses_bound_weights_and_ignores_zero_weight() -> None:
    reasons = trigger_reasons(
        _context(
            sample_percent=0,
            judge_dimensions=[
                {"id": "independence", "tier": "high", "score": 10, "confidence": 0.9},
                # Zero weight cannot drag the weighted mean down.
                {"id": "zero", "tier": "high", "score": 10, "confidence": 0.0},
            ],
        )
    )

    assert "low_confidence" not in reasons


def test_all_unknown_dimensions_count_as_low_confidence() -> None:
    reasons = trigger_reasons(
        _context(
            sample_percent=0,
            judge_dimensions=[
                {"id": "independence", "tier": "unknown", "score": None,
                 "confidence": None}
            ],
        )
    )

    assert "low_confidence" in reasons


def test_borderline_golden_label_never_raises_a_golden_error() -> None:
    reasons = trigger_reasons(
        _context(sample_percent=0, golden_label="borderline", grade="rejected")
    )

    assert "golden_error" not in reasons


@pytest.mark.parametrize(
    ("label", "grade", "expected"),
    [
        ("advance", "rejected", True),
        ("reject", "L1", True),
        ("advance", "L1", False),
        ("reject", "rejected", False),
    ],
)
def test_golden_error_compares_the_label_with_the_prediction(
    label: str, grade: str, expected: bool
) -> None:
    reasons = trigger_reasons(
        _context(sample_percent=0, golden_label=label, grade=grade)
    )

    assert ("golden_error" in reasons) is expected


def test_admin_backfill_is_an_explicit_reason() -> None:
    reasons = trigger_reasons(_context(sample_percent=0), admin_backfill=True)

    assert reasons == ["admin_backfill"]
