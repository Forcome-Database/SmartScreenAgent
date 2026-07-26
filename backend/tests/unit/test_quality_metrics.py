from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from backend.app.services.quality.metrics import (
    AgreementObservation,
    BoundJudgeDimension,
    JudgeObservation,
    QualityItem,
    agreement_metrics,
    classification_metrics,
    confidence_metrics,
    evidence_metrics,
    release_rollup,
    target_result,
)

# Sentinels that must never reach a metric payload. The input types have no
# field able to carry them, and these tests keep it that way.
QUOTE = "candidate said something quotable"
REASONING = "the judge reasoned privately"


def _judge(
    dimension_id: str,
    *,
    tier: str = "high",
    score: Decimal | None = Decimal("10"),
    confidence: Decimal | None = Decimal("0.9"),
    evidence: bool = True,
) -> JudgeObservation:
    return JudgeObservation(
        dimension_id=dimension_id,
        tier=tier,
        score=score,
        confidence=confidence,
        has_validated_evidence=evidence,
    )


def _item(
    *,
    jd_id: int = 1,
    candidate_id: int = 1,
    golden_label: str = "advance",
    grade: str | None = "L1",
    reached_judge: bool = True,
    judge: list[JudgeObservation] | None = None,
) -> QualityItem:
    return QualityItem(
        jd_id=jd_id,
        candidate_id=candidate_id,
        golden_label=golden_label,  # type: ignore[arg-type]
        grade=grade,
        reached_judge=reached_judge,
        judge=judge if judge is not None else [_judge("independence")],
    )


def _strings(value: Any) -> list[str]:
    """Every string anywhere inside a nested metric payload."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for item in value.items() for s in _strings(item)]
    if isinstance(value, (list, tuple)):
        return [s for item in value for s in _strings(item)]
    return []


# --- classification ---------------------------------------------------------


def test_classification_scores_each_confusion_quadrant() -> None:
    result = classification_metrics(
        [
            _item(candidate_id=1, golden_label="advance", grade="L1"),
            _item(candidate_id=2, golden_label="reject", grade="L2"),
            _item(candidate_id=3, golden_label="reject", grade="rejected"),
            _item(candidate_id=4, golden_label="advance", grade="rejected"),
        ]
    )

    # Any non-"rejected" grade predicts advance.
    assert result["confusion"] == {"tp": 1, "fp": 1, "tn": 1, "fn": 1}
    assert result["precision"] == 0.5
    assert result["recall"] == 0.5
    assert result["covered"] == 4


def test_classification_separates_borderline_and_uncovered() -> None:
    result = classification_metrics(
        [
            _item(candidate_id=1, golden_label="advance", grade="L1"),
            _item(candidate_id=2, golden_label="borderline", grade="L1"),
            _item(candidate_id=3, golden_label="advance", grade=None),
            # An unscored borderline item counts as uncovered, not excluded.
            _item(candidate_id=4, golden_label="borderline", grade=None),
        ]
    )

    assert result["labeled_total"] == 4
    assert result["borderline_excluded"] == 1
    assert result["uncovered"] == 2
    assert result["covered"] == 1


def test_classification_returns_null_metrics_without_a_covered_sample() -> None:
    result = classification_metrics([_item(grade=None)])

    assert result["covered"] == 0
    assert result["precision"] is None
    assert result["recall"] is None
    assert result["f1"] is None
    assert result["accuracy"] is None


# --- evidence coverage ------------------------------------------------------


def _dims(*specs: tuple[int, str, str]) -> list[BoundJudgeDimension]:
    return [
        BoundJudgeDimension(jd_id=jd_id, id=name, weight=Decimal(weight))
        for jd_id, name, weight in specs
    ]


def test_evidence_denominator_counts_every_expected_dimension() -> None:
    dimensions = _dims((1, "independence", "10"), (1, "communication", "10"))
    result = evidence_metrics(
        [_item(judge=[_judge("independence"), _judge("communication")])], dimensions
    )

    assert result["participating_candidates"] == 1
    assert result["expected_count"] == 2
    assert result["covered_count"] == 2
    assert result["value"] == 1.0
    assert result["status"] == "ok"


@pytest.mark.parametrize(
    "observation",
    [
        _judge("independence", tier="unknown"),
        _judge("independence", score=None),
        _judge("independence", evidence=False),
    ],
)
def test_unknown_missing_or_unevidenced_dimensions_stay_uncovered(
    observation: JudgeObservation,
) -> None:
    dimensions = _dims((1, "independence", "10"))

    result = evidence_metrics([_item(judge=[observation])], dimensions)

    assert (result["expected_count"], result["covered_count"]) == (1, 0)
    assert result["value"] == 0.0


def test_a_missing_dimension_still_counts_in_the_denominator() -> None:
    dimensions = _dims((1, "independence", "10"), (1, "communication", "10"))

    result = evidence_metrics([_item(judge=[_judge("independence")])], dimensions)

    assert (result["expected_count"], result["covered_count"]) == (2, 1)


def test_hard_filter_rejects_are_counted_but_excluded_from_the_denominator() -> None:
    dimensions = _dims((1, "independence", "10"))

    result = evidence_metrics(
        [
            _item(candidate_id=1, judge=[_judge("independence")]),
            _item(candidate_id=2, grade="rejected", reached_judge=False, judge=[]),
        ],
        dimensions,
    )

    assert result["hard_filter_rejects"] == 1
    assert result["participating_candidates"] == 1
    assert result["expected_count"] == 1


def test_rules_without_judge_dimensions_are_not_applicable() -> None:
    result = evidence_metrics([_item()], [])

    assert result["value"] is None
    assert result["status"] == "not_applicable"


def test_judge_dimensions_with_no_participating_score_are_insufficient() -> None:
    dimensions = _dims((1, "independence", "10"))

    result = evidence_metrics(
        [_item(grade="rejected", reached_judge=False, judge=[])], dimensions
    )

    assert result["value"] is None
    assert result["status"] == "insufficient_data"


# --- confidence reliability -------------------------------------------------


def test_weighted_confidence_is_keyed_by_jd_and_dimension_id() -> None:
    # The same dimension ID carries different weights in different JDs; a lookup
    # keyed only by ID would silently blend them.
    dimensions = _dims((1, "shared", "10"), (2, "shared", "90"), (2, "other", "10"))
    items = [
        _item(jd_id=1, candidate_id=1, judge=[_judge("shared", confidence=Decimal("0.5"))]),
        _item(
            jd_id=2,
            candidate_id=2,
            judge=[
                _judge("shared", confidence=Decimal("1.0")),
                _judge("other", confidence=Decimal("0")),
            ],
        ),
    ]

    result = confidence_metrics(items, dimensions, minimum_bucket_size=1)

    assert result["available_count"] == 2
    # Candidate 2: (1.0*90 + 0*10) / 100 == 0.9, which needs the JD-2 weights.
    means = [b["mean_confidence"] for b in result["bins"] if b["count"]]
    assert Decimal("0.9") in [m for m in means if m is not None]


@pytest.mark.parametrize(
    "confidence",
    [Decimal("1.5"), Decimal("-0.1"), Decimal("NaN"), Decimal("Infinity"), None],
)
def test_unusable_confidence_makes_the_candidate_unavailable(
    confidence: Decimal | None,
) -> None:
    dimensions = _dims((1, "independence", "10"))

    result = confidence_metrics(
        [_item(judge=[_judge("independence", confidence=confidence)])],
        dimensions,
        minimum_bucket_size=1,
    )

    # Never clamped into range — simply not usable.
    assert result["available_count"] == 0
    assert result["confidence_unavailable"] == 1


def test_zero_weight_and_unknown_dimensions_are_excluded_from_the_weighting() -> None:
    dimensions = _dims((1, "counted", "10"), (1, "zero", "0"), (1, "unknown", "10"))

    result = confidence_metrics(
        [
            _item(
                judge=[
                    _judge("counted", confidence=Decimal("0.4")),
                    _judge("zero", confidence=Decimal("1.0")),
                    _judge("unknown", tier="unknown", confidence=Decimal("1.0")),
                ]
            )
        ],
        dimensions,
        minimum_bucket_size=1,
    )

    populated = [b for b in result["bins"] if b["count"]]
    assert len(populated) == 1
    assert populated[0]["mean_confidence"] == Decimal("0.4")


def test_bins_are_the_five_fixed_ranges_with_an_inclusive_top() -> None:
    dimensions = _dims((1, "d", "10"))
    items = [
        _item(candidate_id=index, judge=[_judge("d", confidence=confidence)])
        for index, confidence in enumerate(
            [Decimal("0"), Decimal("0.2"), Decimal("0.4"), Decimal("0.6"), Decimal("1")]
        )
    ]

    result = confidence_metrics(items, dimensions, minimum_bucket_size=1)

    assert [(b["lower"], b["upper"]) for b in result["bins"]] == [
        (Decimal("0"), Decimal("0.2")),
        (Decimal("0.2"), Decimal("0.4")),
        (Decimal("0.4"), Decimal("0.6")),
        (Decimal("0.6"), Decimal("0.8")),
        (Decimal("0.8"), Decimal("1")),
    ]
    assert [b["upper_inclusive"] for b in result["bins"]] == [False, False, False, False, True]
    # Confidence exactly 1 belongs to the final bin, not a sixth one.
    assert [b["count"] for b in result["bins"]] == [1, 1, 1, 1, 1]


def test_small_bins_report_no_accuracy_and_are_excluded_from_ece() -> None:
    dimensions = _dims((1, "d", "10"))
    result = confidence_metrics(
        [_item(judge=[_judge("d", confidence=Decimal("0.9"))])],
        dimensions,
        minimum_bucket_size=10,
    )

    (populated,) = [b for b in result["bins"] if b["count"]]
    assert populated["status"] == "insufficient_data"
    assert populated["decision_accuracy"] is None
    assert populated["absolute_gap"] is None
    assert result["ece"] is None


def test_ece_averages_only_sufficient_bins() -> None:
    dimensions = _dims((1, "d", "10"))
    # Two confident items in the top bin, one of them decided wrongly.
    items = [
        _item(
            candidate_id=1,
            golden_label="advance",
            grade="L1",
            judge=[_judge("d", confidence=Decimal("0.9"))],
        ),
        _item(
            candidate_id=2,
            golden_label="advance",
            grade="rejected",
            judge=[_judge("d", confidence=Decimal("0.9"))],
        ),
        # A lone low-confidence item forms an insufficient bin.
        _item(
            candidate_id=3,
            golden_label="advance",
            grade="L1",
            judge=[_judge("d", confidence=Decimal("0.1"))],
        ),
    ]

    result = confidence_metrics(items, dimensions, minimum_bucket_size=2)

    top = result["bins"][-1]
    assert top["count"] == 2
    assert top["decision_accuracy"] == Decimal("0.5")
    assert top["absolute_gap"] == Decimal("0.4")
    # Only the top bin is sufficient, so ECE equals its own gap.
    assert result["ece"] == Decimal("0.4")


# --- agreement --------------------------------------------------------------


def test_agreement_excludes_hold_from_the_denominator() -> None:
    result = agreement_metrics(
        [
            AgreementObservation(jd_id=1, ai_agreed=True),
            AgreementObservation(jd_id=1, ai_agreed=True),
            AgreementObservation(jd_id=1, ai_agreed=False),
            AgreementObservation(jd_id=1, ai_agreed=None),
        ]
    )

    assert (result["agreed"], result["disagreed"], result["hold"]) == (2, 1, 1)
    assert result["denominator"] == 3
    assert result["agreement_rate"] == pytest.approx(2 / 3)


def test_agreement_rate_is_null_without_a_denominator() -> None:
    result = agreement_metrics([AgreementObservation(jd_id=1, ai_agreed=None)])

    assert result["denominator"] == 0
    assert result["agreement_rate"] is None


# --- targets and rollup -----------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0.80, "meets_target"), (0.75, "meets_target"), (0.74, "below_target")],
)
def test_target_result_compares_against_the_snapshotted_target(
    value: float, expected: str
) -> None:
    assert target_result(value, 0.75, "insufficient_data")["status"] == expected


def test_null_value_takes_the_supplied_null_status() -> None:
    assert target_result(None, 0.75, "insufficient_data")["status"] == "insufficient_data"
    assert target_result(None, 0.95, "not_applicable")["status"] == "not_applicable"


@pytest.mark.parametrize(
    ("f1_status", "evidence_status", "expected"),
    [
        ("meets_target", "meets_target", "meets_target"),
        # A not-applicable evidence result is neutral.
        ("meets_target", "not_applicable", "meets_target"),
        ("below_target", "meets_target", "below_target"),
        ("meets_target", "below_target", "below_target"),
        # Insufficient evidence for a judgement is not a pass.
        ("insufficient_data", "meets_target", "below_target"),
        ("meets_target", "insufficient_data", "below_target"),
    ],
)
def test_release_rollup(f1_status: str, evidence_status: str, expected: str) -> None:
    rollup = release_rollup(
        {"value": None, "target": 0.75, "status": f1_status},
        {"value": None, "target": 0.95, "status": evidence_status},
    )

    assert rollup == expected


# --- leak safety ------------------------------------------------------------


def test_no_metric_payload_can_carry_evidence_or_reasoning() -> None:
    assert set(JudgeObservation.__dataclass_fields__) == {
        "dimension_id",
        "tier",
        "score",
        "confidence",
        "has_validated_evidence",
    }

    dimensions = _dims((1, "independence", "10"))
    items = [_item(judge=[_judge("independence")])]
    payloads = [
        classification_metrics(items),
        evidence_metrics(items, dimensions),
        confidence_metrics(items, dimensions, minimum_bucket_size=1),
        agreement_metrics([AgreementObservation(jd_id=1, ai_agreed=True)]),
    ]

    emitted = [s for payload in payloads for s in _strings(payload)]
    assert QUOTE not in emitted
    assert REASONING not in emitted
