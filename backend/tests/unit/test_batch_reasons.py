from __future__ import annotations

from decimal import Decimal

from backend.app.services.quality.batch import (
    RejectedScore,
    aggregate_rejection_reasons,
)

WEIGHTS = {
    (1, "north_america"): Decimal("60"),
    (1, "education"): Decimal("30"),
    (1, "independence"): Decimal("10"),
    (1, "zero_weight"): Decimal("0"),
}


def _score(
    score_id: int,
    *,
    hard_tags: list[str] | None = None,
    rules: list[tuple[str, float]] | None = None,
    judge: list[tuple[str, str, float | None]] | None = None,
) -> RejectedScore:
    return RejectedScore(
        score_id=score_id,
        jd_id=1,
        hard_filter_result={
            "rejected": bool(hard_tags),
            "audit_entries": [
                {"filter_id": tag, "audit_tag": tag, "rule": {}}
                for tag in (hard_tags or [])
            ],
        },
        rule_dimensions={
            "items": [
                {"id": name, "score": score} for name, score in (rules or [])
            ]
        },
        judge_dimensions=(
            None
            if judge is None
            else {
                "dimensions": [
                    {"id": name, "tier": tier, "score": score}
                    for name, tier, score in judge
                ]
            }
        ),
    )


def _row(rows: list[dict], reason_type: str, key: str) -> dict:
    return next(
        row
        for row in rows
        if row["reason_type"] == reason_type and row["reason_key"] == key
    )


def test_hard_filter_reasons_use_the_persisted_audit_tag() -> None:
    rows = aggregate_rejection_reasons([_score(1, hard_tags=["no_degree"])], WEIGHTS)

    row = _row(rows, "hard_filter", "no_degree")
    assert (row["occurrences"], row["affected_scores"]) == (1, 1)
    assert row["percentage"] == Decimal("100")


def test_a_dimension_is_low_only_strictly_below_half_its_weight() -> None:
    rows = aggregate_rejection_reasons(
        [
            # 29 < 30 => low; 30 == 0.5*60 is NOT low.
            _score(1, rules=[("education", 14), ("north_america", 30)]),
        ],
        WEIGHTS,
    )

    assert _row(rows, "rule_low", "education")["occurrences"] == 1
    assert not [r for r in rows if r["reason_key"] == "north_america"]


def test_zero_weight_dimensions_are_never_low() -> None:
    rows = aggregate_rejection_reasons(
        [_score(1, rules=[("zero_weight", 0)])], WEIGHTS
    )

    assert rows == []


def test_judge_unknown_is_counted_separately_from_low() -> None:
    rows = aggregate_rejection_reasons(
        [_score(1, judge=[("independence", "unknown", None)])], WEIGHTS
    )

    assert _row(rows, "judge_unknown", "independence")["occurrences"] == 1
    assert not [r for r in rows if r["reason_type"] == "judge_low"]


def test_judge_low_uses_the_bound_weight() -> None:
    rows = aggregate_rejection_reasons(
        [_score(1, judge=[("independence", "low", 4)])], WEIGHTS
    )

    assert _row(rows, "judge_low", "independence")["occurrences"] == 1


def test_repeated_reasons_in_one_score_count_occurrences_but_one_affected_score() -> None:
    rows = aggregate_rejection_reasons(
        [_score(1, hard_tags=["no_degree", "no_degree"])], WEIGHTS
    )

    row = _row(rows, "hard_filter", "no_degree")
    assert row["occurrences"] == 2
    assert row["affected_scores"] == 1


def test_percentages_divide_by_total_rejected_and_may_overlap() -> None:
    rows = aggregate_rejection_reasons(
        [
            _score(1, hard_tags=["no_degree"], rules=[("education", 1)]),
            _score(2, hard_tags=["no_degree"]),
            _score(3, rules=[("education", 1)]),
            _score(4),
        ],
        WEIGHTS,
    )

    assert _row(rows, "hard_filter", "no_degree")["percentage"] == Decimal("50")
    assert _row(rows, "rule_low", "education")["percentage"] == Decimal("50")
    # Overlapping reasons: the percentages deliberately exceed 100 in total.
    assert sum(row["percentage"] for row in rows) == Decimal("100")


def test_empty_population_yields_no_rows_and_no_division() -> None:
    assert aggregate_rejection_reasons([], WEIGHTS) == []


def test_ordering_is_deterministic() -> None:
    rows = aggregate_rejection_reasons(
        [
            _score(1, hard_tags=["b_tag", "b_tag"]),
            _score(2, hard_tags=["b_tag"]),
            _score(3, hard_tags=["a_tag"]),
            _score(4, rules=[("education", 1)]),
        ],
        WEIGHTS,
    )

    # affected desc, then occurrences desc, then type asc, then key asc.
    assert [(row["reason_type"], row["reason_key"]) for row in rows] == [
        ("hard_filter", "b_tag"),
        ("hard_filter", "a_tag"),
        ("rule_low", "education"),
    ]


def test_rows_carry_no_free_text() -> None:
    rows = aggregate_rejection_reasons(
        [_score(1, hard_tags=["no_degree"], judge=[("independence", "low", 1)])],
        WEIGHTS,
    )

    for row in rows:
        assert set(row) == {
            "reason_type",
            "reason_key",
            "occurrences",
            "affected_scores",
            "percentage",
        }
