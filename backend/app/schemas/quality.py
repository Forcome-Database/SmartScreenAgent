from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, PlainSerializer

from backend.app.services.quality.releases import canonical_json

# Aggregates only. No candidate identifier, ciphertext, object key, evidence
# quote, reasoning string, or snapshot entry may be declared in this module.


def _as_json_number(value: Decimal | None) -> float | int | None:
    """Emit a ratio as a JSON number without binary-float drift.

    `float(Decimal("0.75"))` is fine, but formatting a computed Decimal through
    binary float can yield 0.7500000000000001. Reusing the canonical token keeps
    the wire value identical to what the fingerprint hashed.
    """
    if value is None:
        return None
    parsed: float | int = json.loads(canonical_json(value))
    return parsed


Ratio = Annotated[Decimal | None, PlainSerializer(_as_json_number, when_used="json")]
Number = Annotated[Decimal, PlainSerializer(_as_json_number, when_used="json")]

TargetStatus = Literal[
    "meets_target", "below_target", "insufficient_data", "not_applicable"
]


class Confusion(BaseModel):
    tp: int
    fp: int
    tn: int
    fn: int


class ClassificationMetrics(BaseModel):
    labeled_total: int
    covered: int
    uncovered: int
    borderline_excluded: int
    confusion: Confusion
    precision: Ratio
    recall: Ratio
    f1: Ratio
    accuracy: Ratio


class EvidenceMetrics(BaseModel):
    participating_candidates: int
    hard_filter_rejects: int
    expected_count: int
    covered_count: int
    value: Ratio
    status: Literal["ok", "insufficient_data", "not_applicable"]


class ConfidenceBin(BaseModel):
    lower: Number
    upper: Number
    upper_inclusive: bool
    count: int
    mean_confidence: Ratio
    decision_accuracy: Ratio
    absolute_gap: Ratio
    status: Literal["ok", "insufficient_data"]


class ConfidenceMetrics(BaseModel):
    available_count: int
    confidence_unavailable: int
    bins: list[ConfidenceBin]
    ece: Ratio


class AgreementMetrics(BaseModel):
    agreed: int
    disagreed: int
    hold: int
    denominator: int
    agreement_rate: Ratio


class TargetResult(BaseModel):
    value: Ratio
    target: Number
    status: TargetStatus


class ReleaseOperationTotals(BaseModel):
    """Ledger and throughput facts attributed to this release's bindings."""

    attempt_count: int
    succeeded_count: int
    failed_count: int
    abandoned_count: int
    unknown_usage_count: int
    known_cost_cny: Number
    p50_latency_ms: Ratio
    p95_latency_ms: Ratio
    scored_count: int
    scores_per_day: Ratio


class ReleaseOperationDelta(BaseModel):
    absolute: Ratio
    percentage: Ratio


class ReleaseOperations(BaseModel):
    current: ReleaseOperationTotals
    previous: ReleaseOperationTotals
    cost_delta: ReleaseOperationDelta
    attempt_delta: ReleaseOperationDelta


class ReleaseJDSelection(BaseModel):
    jd_id: int
    jd_code: str
    rule_version_id: int


class ReleaseCreator(BaseModel):
    user_id: int
    display_name: str


class QualityReleasePreview(BaseModel):
    window_start: datetime
    window_end: datetime
    selected: list[ReleaseJDSelection]
    golden_total: int
    golden_advance: int
    golden_reject: int
    golden_borderline: int
    score_covered: int
    score_uncovered: int
    targets: dict[str, Any]
    input_fingerprint: str


class QualityReleaseJDMetrics(BaseModel):
    jd_id: int
    jd_code: str
    rule_version_id: int
    classification: ClassificationMetrics
    evidence: EvidenceMetrics
    confidence: ConfidenceMetrics
    agreement: AgreementMetrics
    f1_target_result: TargetResult
    evidence_target_result: TargetResult


class QualityReleaseDetail(BaseModel):
    id: int
    status: Literal["meets_target", "below_target"]
    golden_snapshot_sha256: str
    golden_snapshot_item_count: int
    window_start: datetime
    window_end: datetime
    created_at: datetime
    created_by: ReleaseCreator
    targets: dict[str, Any]
    classification: ClassificationMetrics
    evidence: EvidenceMetrics
    confidence: ConfidenceMetrics
    agreement: AgreementMetrics
    f1_target_result: TargetResult
    evidence_target_result: TargetResult
    operations: ReleaseOperations
    by_jd: list[QualityReleaseJDMetrics]


class QualityReleaseSummary(BaseModel):
    id: int
    status: Literal["meets_target", "below_target"]
    window_start: datetime
    window_end: datetime
    created_at: datetime
    created_by: ReleaseCreator
    golden_snapshot_sha256: str


class QualityReleaseList(BaseModel):
    items: list[QualityReleaseSummary]
    page: int
    page_size: int
    total: int
