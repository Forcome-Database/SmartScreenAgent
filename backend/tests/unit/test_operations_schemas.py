from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

from backend.app.schemas.operations import (
    BudgetSnapshot,
    OperationsBreakdown,
    OperationsSeriesPoint,
    OperationsTotals,
    UsageItem,
    UsagePage,
)

STARTED_AT = datetime(2026, 7, 26, 5, 0, tzinfo=UTC)


def _totals() -> OperationsTotals:
    return OperationsTotals(
        attempt_count=3,
        known_cost_cny=Decimal("1.250000000000"),
        known_token_total=45,
        unknown_usage_count=1,
        succeeded_count=2,
        failed_count=1,
        abandoned_count=0,
        pending_count=0,
        p50_latency_ms=Decimal("120.5"),
        p95_latency_ms=None,
        last_completed_at=STARTED_AT,
    )


def _usage_item() -> UsageItem:
    return UsageItem(
        id=1,
        call_group_id=uuid4(),
        trace_id="trace-1",
        ingestion_job_id=None,
        score_id=None,
        jd_id=None,
        rule_version_id=None,
        operation="judge",
        attempt_role="primary",
        requested_model="test-judge",
        actual_model=None,
        prompt_version="resume_judge_v1",
        status="succeeded",
        input_tokens=10,
        output_tokens=5,
        input_price_cny_per_million=Decimal("1.000000"),
        output_price_cny_per_million=Decimal("2.000000"),
        estimated_cost_cny=Decimal("0.000020000000"),
        latency_ms=12,
        error_code=None,
        started_at=STARTED_AT,
        finished_at=None,
    )


def test_decimals_serialize_as_strings_without_float_conversion() -> None:
    payload = json.loads(_totals().model_dump_json())

    # Floats would silently round money; these must stay exact strings.
    assert payload["known_cost_cny"] == "1.250000000000"
    assert payload["p50_latency_ms"] == "120.5"
    assert payload["p95_latency_ms"] is None


def test_utc_datetimes_keep_their_offset() -> None:
    payload = json.loads(_totals().model_dump_json())

    assert payload["last_completed_at"] in {
        "2026-07-26T05:00:00Z",
        "2026-07-26T05:00:00+00:00",
    }


def test_series_point_serializes_a_local_calendar_date() -> None:
    point = OperationsSeriesPoint(
        local_date=date(2026, 7, 26),
        attempt_count=2,
        known_cost_cny=Decimal("0.5"),
        unknown_usage_count=0,
    )

    assert json.loads(point.model_dump_json())["local_date"] == "2026-07-26"


def test_breakdown_key_carries_the_unknown_placeholder() -> None:
    breakdown = OperationsBreakdown(
        key="(unknown)",
        attempt_count=1,
        known_cost_cny=Decimal("0"),
        unknown_usage_count=1,
    )

    assert json.loads(breakdown.model_dump_json())["key"] == "(unknown)"


def test_budget_snapshot_allows_a_null_ratio_for_a_zero_budget() -> None:
    snapshot = BudgetSnapshot(
        scope="daily",
        period_start=STARTED_AT,
        period_end=STARTED_AT,
        budget_cny=Decimal("0"),
        spend_cny=Decimal("1"),
        ratio=None,
        unknown_cost_count=0,
        state="exceeded",
    )

    assert json.loads(snapshot.model_dump_json())["ratio"] is None


def test_usage_item_exposes_exactly_the_declared_metadata_fields() -> None:
    assert set(UsageItem.model_fields) == {
        "id",
        "call_group_id",
        "trace_id",
        "ingestion_job_id",
        "score_id",
        "jd_id",
        "rule_version_id",
        "operation",
        "attempt_role",
        "requested_model",
        "actual_model",
        "prompt_version",
        "status",
        "input_tokens",
        "output_tokens",
        "input_price_cny_per_million",
        "output_price_cny_per_million",
        "estimated_cost_cny",
        "latency_ms",
        "error_code",
        "started_at",
        "finished_at",
    }


def test_usage_page_has_exactly_the_declared_fields() -> None:
    assert set(UsagePage.model_fields) == {"items", "page", "page_size", "total"}


def test_candidate_content_cannot_ride_along_in_a_usage_page() -> None:
    page = UsagePage(items=[_usage_item()], page=1, page_size=20, total=1)

    # Even if a caller tries to smuggle them in, there is no field to hold them.
    smuggled = UsageItem.model_validate(
        {
            **_usage_item().model_dump(),
            "candidate_name": "private-name",
            "object_key": "object/key",
            "prompt": "resume text",
        }
    )
    body = page.model_dump_json() + smuggled.model_dump_json()

    assert "private-name" not in body
    assert "object/key" not in body
    assert "resume text" not in body
