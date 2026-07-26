from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

LOW_SCORE_RATIO = Decimal("0.5")
UNKNOWN_TIER = "unknown"

REASON_HARD_FILTER = "hard_filter"
REASON_RULE_LOW = "rule_low"
REASON_JUDGE_LOW = "judge_low"
REASON_JUDGE_UNKNOWN = "judge_unknown"


@dataclass(frozen=True)
class RejectedScore:
    """A rejected score reduced to the fields a reason can be derived from."""

    score_id: int
    jd_id: int
    hard_filter_result: dict[str, Any]
    rule_dimensions: dict[str, Any]
    judge_dimensions: dict[str, Any] | None


def _numeric(value: object) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = Decimal(str(value))
    except (ArithmeticError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _is_low(score: object, weight: Decimal | None) -> bool:
    """Strictly below half the bound weight.

    Equality is deliberately not low: a dimension that scored exactly half its
    weight met the midpoint, and calling that a rejection reason would inflate
    every report.
    """
    if weight is None or weight <= 0:
        return False
    numeric = _numeric(score)
    return numeric is not None and numeric < LOW_SCORE_RATIO * weight


def _entries(payload: dict[str, Any] | None, key: str) -> list[dict[str, Any]]:
    entries = (payload or {}).get(key)
    return [entry for entry in entries if isinstance(entry, dict)] if isinstance(
        entries, list
    ) else []


def _reasons_for(
    score: RejectedScore, weights: dict[tuple[int, str], Decimal]
) -> list[tuple[str, str]]:
    reasons: list[tuple[str, str]] = []

    for entry in _entries(score.hard_filter_result, "audit_entries"):
        tag = entry.get("audit_tag")
        if isinstance(tag, str) and tag:
            reasons.append((REASON_HARD_FILTER, tag))

    for entry in _entries(score.rule_dimensions, "items"):
        dimension_id = entry.get("id")
        if not isinstance(dimension_id, str):
            continue
        if _is_low(entry.get("score"), weights.get((score.jd_id, dimension_id))):
            reasons.append((REASON_RULE_LOW, dimension_id))

    for entry in _entries(score.judge_dimensions, "dimensions"):
        dimension_id = entry.get("id")
        if not isinstance(dimension_id, str):
            continue
        if str(entry.get("tier") or UNKNOWN_TIER) == UNKNOWN_TIER:
            reasons.append((REASON_JUDGE_UNKNOWN, dimension_id))
        elif _is_low(entry.get("score"), weights.get((score.jd_id, dimension_id))):
            reasons.append((REASON_JUDGE_LOW, dimension_id))

    return reasons


def aggregate_rejection_reasons(
    scores: list[RejectedScore], weights: dict[tuple[int, str], Decimal]
) -> list[dict[str, Any]]:
    """Count why a rejected population was rejected, without reading any text.

    Percentages are shares of the rejected population and deliberately may sum
    past 100: one candidate can fail several checks at once.
    """
    occurrences: dict[tuple[str, str], int] = {}
    affected: dict[tuple[str, str], set[int]] = {}

    for score in scores:
        for reason in _reasons_for(score, weights):
            occurrences[reason] = occurrences.get(reason, 0) + 1
            affected.setdefault(reason, set()).add(score.score_id)

    total = len(scores)
    # Sort on a typed tuple, then project: most-affected first, then most
    # occurrences, then a stable alphabetical tie-break so equal rows never
    # shuffle between runs.
    ordered = sorted(
        (
            (-len(affected[reason]), -count, reason[0], reason[1])
            for reason, count in occurrences.items()
        )
    )
    return [
        {
            "reason_type": reason_type,
            "reason_key": reason_key,
            "occurrences": -negative_count,
            "affected_scores": -negative_affected,
            "percentage": (
                Decimal(-negative_affected) / Decimal(total) * 100 if total else None
            ),
        }
        for negative_affected, negative_count, reason_type, reason_key in ordered
    ]
