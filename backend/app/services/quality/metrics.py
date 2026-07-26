from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

from backend.app.services.golden_set import metric_stats

GoldenLabel = Literal["advance", "reject", "borderline"]
TargetStatus = Literal[
    "meets_target", "below_target", "insufficient_data", "not_applicable"
]
CoverageStatus = Literal["ok", "insufficient_data", "not_applicable"]

UNKNOWN_TIER = "unknown"
REJECTED_GRADE = "rejected"

# Five fixed bins; only the last one includes its upper bound so that a
# confidence of exactly 1 has a home instead of falling off the end.
BIN_EDGES: tuple[tuple[Decimal, Decimal], ...] = (
    (Decimal("0"), Decimal("0.2")),
    (Decimal("0.2"), Decimal("0.4")),
    (Decimal("0.4"), Decimal("0.6")),
    (Decimal("0.6"), Decimal("0.8")),
    (Decimal("0.8"), Decimal("1")),
)


@dataclass(frozen=True)
class BoundJudgeDimension:
    jd_id: int
    id: str
    weight: Decimal


@dataclass(frozen=True)
class JudgeObservation:
    """A persisted judge dimension, stripped of every quote and reasoning string."""

    dimension_id: str
    tier: str
    score: Decimal | None
    confidence: Decimal | None
    has_validated_evidence: bool


@dataclass(frozen=True)
class QualityItem:
    jd_id: int
    candidate_id: int
    golden_label: GoldenLabel
    grade: str | None
    reached_judge: bool
    judge: list[JudgeObservation] = field(default_factory=list)


@dataclass(frozen=True)
class AgreementObservation:
    jd_id: int
    ai_agreed: bool | None


def _predicts_advance(grade: str) -> bool:
    return grade != REJECTED_GRADE


def _is_usable_confidence(value: Decimal | None) -> bool:
    """Out-of-range or non-finite confidence is unusable — never clamped."""
    return value is not None and value.is_finite() and Decimal("0") <= value <= Decimal("1")


def _is_known(observation: JudgeObservation) -> bool:
    return observation.tier != UNKNOWN_TIER and observation.score is not None


def classification_metrics(items: list[QualityItem]) -> dict[str, Any]:
    counts = {"tp": 0, "fp": 0, "tn": 0, "fn": 0}
    borderline_excluded = 0
    uncovered = 0

    for item in items:
        # Order matters: an unscored item is uncovered even when borderline.
        if item.grade is None:
            uncovered += 1
        elif item.golden_label == "borderline":
            borderline_excluded += 1
        elif item.golden_label == "advance":
            counts["tp" if _predicts_advance(item.grade) else "fn"] += 1
        else:
            counts["fp" if _predicts_advance(item.grade) else "tn"] += 1

    return {
        "labeled_total": len(items),
        "covered": sum(counts.values()),
        "uncovered": uncovered,
        "borderline_excluded": borderline_excluded,
        **metric_stats(counts["tp"], counts["fp"], counts["tn"], counts["fn"]),
    }


def _expected_by_jd(
    dimensions: list[BoundJudgeDimension],
) -> dict[int, list[BoundJudgeDimension]]:
    grouped: dict[int, list[BoundJudgeDimension]] = {}
    for dimension in dimensions:
        grouped.setdefault(dimension.jd_id, []).append(dimension)
    return grouped


def evidence_metrics(
    items: list[QualityItem], expected_dimensions: list[BoundJudgeDimension]
) -> dict[str, Any]:
    expected_by_jd = _expected_by_jd(expected_dimensions)
    participating = [item for item in items if item.reached_judge]
    hard_filter_rejects = sum(
        1 for item in items if item.grade is not None and not item.reached_judge
    )

    expected_count = 0
    covered_count = 0
    for item in participating:
        observations = {entry.dimension_id: entry for entry in item.judge}
        for dimension in expected_by_jd.get(item.jd_id, []):
            expected_count += 1
            observation = observations.get(dimension.id)
            # A missing or unknown dimension stays in the denominator.
            if (
                observation is not None
                and _is_known(observation)
                and observation.has_validated_evidence
            ):
                covered_count += 1

    if not expected_dimensions:
        status: CoverageStatus = "not_applicable"
        value: float | None = None
    elif expected_count == 0:
        status = "insufficient_data"
        value = None
    else:
        status = "ok"
        value = covered_count / expected_count

    return {
        "participating_candidates": len(participating),
        "hard_filter_rejects": hard_filter_rejects,
        "expected_count": expected_count,
        "covered_count": covered_count,
        "value": value,
        "status": status,
    }


def _weighted_confidence(
    item: QualityItem, weights: dict[tuple[int, str], Decimal]
) -> Decimal | None:
    total_weight = Decimal("0")
    weighted_sum = Decimal("0")
    for observation in item.judge:
        weight = weights.get((item.jd_id, observation.dimension_id))
        if weight is None or weight <= 0:
            continue
        if not _is_known(observation) or not _is_usable_confidence(observation.confidence):
            continue
        assert observation.confidence is not None
        total_weight += weight
        weighted_sum += observation.confidence * weight
    if total_weight == 0:
        return None
    return weighted_sum / total_weight


def _bin_index(confidence: Decimal) -> int:
    for index, (_lower, upper) in enumerate(BIN_EDGES):
        if confidence < upper:
            return index
    return len(BIN_EDGES) - 1


def confidence_metrics(
    items: list[QualityItem],
    dimensions: list[BoundJudgeDimension],
    minimum_bucket_size: int,
) -> dict[str, Any]:
    # Keyed by (jd_id, id): the same dimension name can carry different weights
    # in different JDs, and blending them would silently misweight the mean.
    weights = {(d.jd_id, d.id): d.weight for d in dimensions}

    buckets: list[list[tuple[Decimal, int]]] = [[] for _ in BIN_EDGES]
    unavailable = 0

    for item in items:
        if item.golden_label == "borderline" or item.grade is None or not item.reached_judge:
            continue
        confidence = _weighted_confidence(item, weights)
        if confidence is None:
            unavailable += 1
            continue
        correct = int(_predicts_advance(item.grade) == (item.golden_label == "advance"))
        buckets[_bin_index(confidence)].append((confidence, correct))

    bins: list[dict[str, Any]] = []
    sufficient: list[dict[str, Any]] = []
    for (lower, upper), entries in zip(BIN_EDGES, buckets, strict=True):
        count = len(entries)
        is_sufficient = count >= minimum_bucket_size and count > 0
        mean = (
            sum((entry[0] for entry in entries), Decimal("0")) / count if count else None
        )
        accuracy = (
            sum((Decimal(entry[1]) for entry in entries), Decimal("0")) / count
            if is_sufficient
            else None
        )
        gap = abs(mean - accuracy) if mean is not None and accuracy is not None else None
        entry_payload = {
            "lower": lower,
            "upper": upper,
            "upper_inclusive": upper == Decimal("1"),
            "count": count,
            "mean_confidence": mean,
            "decision_accuracy": accuracy,
            "absolute_gap": gap,
            "status": "ok" if is_sufficient else "insufficient_data",
        }
        bins.append(entry_payload)
        if is_sufficient:
            sufficient.append(entry_payload)

    total_sufficient = sum(item["count"] for item in sufficient)
    ece = (
        sum(
            (
                Decimal(item["count"]) / Decimal(total_sufficient) * item["absolute_gap"]
                for item in sufficient
            ),
            Decimal("0"),
        )
        if total_sufficient
        else None
    )

    return {
        "available_count": sum(len(entries) for entries in buckets),
        "confidence_unavailable": unavailable,
        "bins": bins,
        "ece": ece,
    }


def agreement_metrics(observations: list[AgreementObservation]) -> dict[str, Any]:
    agreed = sum(1 for item in observations if item.ai_agreed is True)
    disagreed = sum(1 for item in observations if item.ai_agreed is False)
    hold = sum(1 for item in observations if item.ai_agreed is None)
    denominator = agreed + disagreed

    return {
        "agreed": agreed,
        "disagreed": disagreed,
        "hold": hold,
        "denominator": denominator,
        "agreement_rate": (agreed / denominator) if denominator else None,
    }


def target_result(
    value: float | None, target: float, null_status: TargetStatus
) -> dict[str, Any]:
    if value is None:
        status: TargetStatus = null_status
    else:
        status = "meets_target" if value >= target else "below_target"
    return {"value": value, "target": target, "status": status}


def release_rollup(
    f1_result: dict[str, Any], evidence_result: dict[str, Any]
) -> Literal["meets_target", "below_target"]:
    """Only the aggregate targets decide a release; per-JD results are diagnostic.

    `not_applicable` is neutral — a rule set with no judge dimensions cannot fail
    an evidence target it does not have.
    """
    for result in (f1_result, evidence_result):
        if result["status"] in {"below_target", "insufficient_data"}:
            return "below_target"
    return "meets_target"
