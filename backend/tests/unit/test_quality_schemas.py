from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

from backend.app.schemas.quality import (
    AgreementMetrics,
    ClassificationMetrics,
    ConfidenceBin,
    ConfidenceMetrics,
    Confusion,
    EvidenceMetrics,
    QualityReleaseDetail,
    QualityReleaseList,
    QualityReleasePreview,
    ReleaseCreator,
    ReleaseJDSelection,
    TargetResult,
)

MOMENT = datetime(2026, 7, 26, 5, 0, tzinfo=UTC)


def _classification(**overrides) -> ClassificationMetrics:
    payload = {
        "labeled_total": 10,
        "covered": 8,
        "uncovered": 1,
        "borderline_excluded": 1,
        "confusion": Confusion(tp=4, fp=1, tn=2, fn=1),
        "precision": Decimal("0.8"),
        "recall": Decimal("0.8"),
        "f1": Decimal("0.8"),
        "accuracy": Decimal("0.75"),
    }
    payload.update(overrides)
    return ClassificationMetrics(**payload)


def test_ratios_serialize_as_json_numbers_without_binary_float_drift() -> None:
    body = json.loads(_classification().model_dump_json())

    assert body["precision"] == 0.8
    assert not isinstance(body["precision"], str)
    # A float round trip of Decimal("0.75") must not become 0.7500000000000001.
    assert json.dumps(body["accuracy"]) == "0.75"


def test_null_ratios_keep_their_denominators() -> None:
    body = json.loads(
        _classification(precision=None, recall=None, f1=None, accuracy=None).model_dump_json()
    )

    assert body["precision"] is None
    # The sample size survives even when the ratio cannot be computed.
    assert body["covered"] == 8
    assert body["confusion"] == {"tp": 4, "fp": 1, "tn": 2, "fn": 1}


def test_evidence_metrics_keep_status_alongside_a_null_value() -> None:
    body = json.loads(
        EvidenceMetrics(
            participating_candidates=0,
            hard_filter_rejects=2,
            expected_count=0,
            covered_count=0,
            value=None,
            status="insufficient_data",
        ).model_dump_json()
    )

    assert body["value"] is None
    assert body["status"] == "insufficient_data"
    assert body["hard_filter_rejects"] == 2


def test_confidence_bin_reports_bounds_and_inclusivity() -> None:
    body = json.loads(
        ConfidenceBin(
            lower=Decimal("0.8"),
            upper=Decimal("1"),
            upper_inclusive=True,
            count=12,
            mean_confidence=Decimal("0.9"),
            decision_accuracy=Decimal("0.5"),
            absolute_gap=Decimal("0.4"),
            status="ok",
        ).model_dump_json()
    )

    assert (body["lower"], body["upper"]) == (0.8, 1)
    assert body["upper_inclusive"] is True
    assert body["absolute_gap"] == 0.4


def test_preview_exposes_exactly_its_declared_fields() -> None:
    assert set(QualityReleasePreview.model_fields) == {
        "window_start",
        "window_end",
        "selected",
        "golden_total",
        "golden_advance",
        "golden_reject",
        "golden_borderline",
        "score_covered",
        "score_uncovered",
        "targets",
        "input_fingerprint",
    }


def test_detail_exposes_exactly_its_declared_fields() -> None:
    assert set(QualityReleaseDetail.model_fields) == {
        "id",
        "status",
        "golden_snapshot_sha256",
        "golden_snapshot_item_count",
        "window_start",
        "window_end",
        "created_at",
        "created_by",
        "targets",
        "classification",
        "evidence",
        "confidence",
        "agreement",
        "f1_target_result",
        "evidence_target_result",
        "by_jd",
    }


def test_list_is_paginated() -> None:
    assert set(QualityReleaseList.model_fields) == {"items", "page", "page_size", "total"}


def test_no_release_payload_can_carry_candidate_identity_or_content() -> None:
    detail = QualityReleaseDetail(
        id=1,
        status="meets_target",
        golden_snapshot_sha256="a" * 64,
        golden_snapshot_item_count=10,
        window_start=MOMENT,
        window_end=MOMENT,
        created_at=MOMENT,
        created_by=ReleaseCreator(user_id=7, display_name="Reviewer"),
        targets={"f1_target": 0.75},
        classification=_classification(),
        evidence=EvidenceMetrics(
            participating_candidates=8,
            hard_filter_rejects=1,
            expected_count=16,
            covered_count=15,
            value=Decimal("0.9375"),
            status="ok",
        ),
        confidence=ConfidenceMetrics(
            available_count=8, confidence_unavailable=0, bins=[], ece=None
        ),
        agreement=AgreementMetrics(
            agreed=5, disagreed=1, hold=2, denominator=6, agreement_rate=Decimal("0.8")
        ),
        f1_target_result=TargetResult(
            value=Decimal("0.8"), target=Decimal("0.75"), status="meets_target"
        ),
        evidence_target_result=TargetResult(
            value=Decimal("0.9375"), target=Decimal("0.95"), status="below_target"
        ),
        by_jd=[],
    )

    body = detail.model_dump_json()

    # Aggregates only: an individual candidate must not be identifiable, and no
    # snapshot entry, quote, or reasoning may ride along.
    for forbidden in ("candidate_id", "candidate_name", "entries", "evidence_quotes",
                      "reasoning", "object_key", "name_cipher"):
        assert forbidden not in body


def test_selection_carries_only_binding_identifiers() -> None:
    assert set(ReleaseJDSelection.model_fields) == {"jd_id", "jd_code", "rule_version_id"}
