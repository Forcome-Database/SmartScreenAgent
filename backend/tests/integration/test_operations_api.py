from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import JD, LLMUsageAttempt

pytestmark = pytest.mark.integration

SUMMARY = "/api/v1/operations/summary"
USAGE = "/api/v1/operations/usage"

# Aggregates are asserted through the `7d` window: it is a fixed offset from
# `now`, so it cannot go flaky when the suite happens to run just after local
# midnight (which would leave `today` almost empty). `today`'s boundary maths is
# covered deterministically in `test_operations_windows.py`.
NOW = datetime.now(UTC)
IN_CURRENT = NOW - timedelta(days=1)
IN_PREVIOUS = NOW - timedelta(days=8)


async def _seed(
    db: AsyncSession,
    *,
    started_at: datetime,
    status: str = "succeeded",
    cost: Decimal | None = Decimal("1"),
    input_tokens: int | None = 10,
    output_tokens: int | None = 5,
    latency_ms: int | None = 100,
    operation: str = "judge",
    attempt_role: str = "primary",
    requested_model: str = "test-judge",
    actual_model: str | None = "test-judge",
    trace_id: str | None = None,
    jd_id: int | None = None,
    score_id: int | None = None,
    ingestion_job_id: int | None = None,
) -> LLMUsageAttempt:
    attempt = LLMUsageAttempt(
        call_group_id=uuid4(),
        trace_id=trace_id or f"trace-{uuid4().hex[:8]}",
        ingestion_job_id=ingestion_job_id,
        score_id=score_id,
        jd_id=jd_id,
        operation=operation,
        attempt_role=attempt_role,
        requested_model=requested_model,
        actual_model=actual_model,
        prompt_version="resume_judge_v1",
        status=status,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        input_price_cny_per_million=Decimal("1.000000"),
        output_price_cny_per_million=Decimal("2.000000"),
        estimated_cost_cny=cost,
        latency_ms=latency_ms,
        error_code=None if status == "succeeded" else "provider_unavailable",
        started_at=started_at,
        finished_at=None if status == "pending" else started_at + timedelta(seconds=1),
    )
    db.add(attempt)
    await db.flush()
    return attempt


async def _seed_summary_fixture(db: AsyncSession) -> None:
    await _seed(db, started_at=IN_CURRENT, cost=Decimal("1.5"), latency_ms=100)
    await _seed(
        db,
        started_at=IN_CURRENT,
        status="unavailable",
        cost=Decimal("0.25"),
        input_tokens=None,
        output_tokens=None,
        latency_ms=300,
    )
    await _seed(
        db,
        started_at=IN_CURRENT,
        cost=Decimal("0.25"),
        input_tokens=2,
        output_tokens=3,
        latency_ms=200,
        operation="extract",
        attempt_role="fallback",
        requested_model="test-extract",
        actual_model=None,
    )
    # Deliberately malformed: pending rows never carry usable latency, so this
    # must be excluded from the percentiles even though latency_ms is set.
    await _seed(
        db,
        started_at=IN_CURRENT,
        status="pending",
        cost=None,
        input_tokens=None,
        output_tokens=None,
        latency_ms=99999,
    )
    await _seed(db, started_at=IN_PREVIOUS, cost=Decimal("4"), latency_ms=50)
    await db.commit()


async def test_summary_requires_an_operations_role(client, db_session, auth_headers):
    assert (await client.get(f"{SUMMARY}?window=7d")).status_code == 401
    assert (await client.get(f"{USAGE}")).status_code == 401

    hr = await auth_headers("hr")
    assert (await client.get(f"{SUMMARY}?window=7d", headers=hr)).status_code == 403

    for role in ("hr_lead", "admin"):
        headers = await auth_headers(role)
        assert (await client.get(f"{SUMMARY}?window=7d", headers=headers)).status_code == 200


async def test_summary_aggregates_current_and_previous_windows(
    client, db_session, auth_headers
):
    await _seed_summary_fixture(db_session)

    body = (
        await client.get(f"{SUMMARY}?window=7d", headers=await auth_headers("hr_lead"))
    ).json()

    current = body["current"]
    assert current["attempt_count"] == 4
    assert Decimal(current["known_cost_cny"]) == Decimal("2.000000000000")
    assert current["known_token_total"] == 20
    assert current["unknown_usage_count"] == 2
    assert (current["succeeded_count"], current["failed_count"]) == (2, 1)
    assert (current["abandoned_count"], current["pending_count"]) == (0, 1)
    assert current["last_completed_at"] is not None

    previous = body["previous"]
    assert previous["attempt_count"] == 1
    assert Decimal(previous["known_cost_cny"]) == Decimal("4.000000000000")

    assert Decimal(body["cost_delta"]["absolute"]) == Decimal("-2.000000000000")
    assert Decimal(body["cost_delta"]["percentage"]) == Decimal("-50")
    assert body["attempt_delta"]["absolute"] == "3"


async def test_percentiles_ignore_pending_and_unknown_latency(
    client, db_session, auth_headers
):
    await _seed_summary_fixture(db_session)

    current = (
        await client.get(f"{SUMMARY}?window=7d", headers=await auth_headers("hr_lead"))
    ).json()["current"]

    # Terminal latencies are 100/200/300; the pending 99999 must not appear.
    assert Decimal(current["p50_latency_ms"]) == Decimal("200")
    assert Decimal(current["p95_latency_ms"]) == Decimal("290")


async def test_summary_breakdowns_series_and_budgets(client, db_session, auth_headers):
    await _seed_summary_fixture(db_session)

    body = (
        await client.get(f"{SUMMARY}?window=7d", headers=await auth_headers("hr_lead"))
    ).json()

    by_operation = {row["key"]: row["attempt_count"] for row in body["by_operation"]}
    assert by_operation == {"judge": 3, "extract": 1}
    by_role = {row["key"]: row["attempt_count"] for row in body["by_attempt_role"]}
    assert by_role == {"primary": 3, "fallback": 1}
    # A null actual model is reported under an explicit placeholder.
    assert "(unknown)" in {row["key"] for row in body["by_actual_model"]}
    assert {row["key"] for row in body["by_outcome"]} == {
        "succeeded",
        "unavailable",
        "pending",
    }

    assert sum(point["attempt_count"] for point in body["daily_series"]) == 4
    assert all(len(point["local_date"]) == 10 for point in body["daily_series"])

    assert {snapshot["scope"] for snapshot in body["budgets"]} == {"daily", "monthly"}


async def test_end_boundary_is_excluded_from_the_window(client, db_session, auth_headers):
    # Exactly on the exclusive end of the 7d current window's start boundary.
    await _seed(db_session, started_at=NOW - timedelta(days=7, seconds=1))
    await db_session.commit()

    body = (
        await client.get(f"{SUMMARY}?window=7d", headers=await auth_headers("hr_lead"))
    ).json()

    assert body["current"]["attempt_count"] == 0
    assert body["previous"]["attempt_count"] == 1


async def test_unsupported_window_returns_a_stable_error(client, db_session, auth_headers):
    response = await client.get(
        f"{SUMMARY}?window=yesterday", headers=await auth_headers("hr_lead")
    )

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "code": "invalid_operations_window",
        "message": "Unsupported operations window",
    }


@pytest.mark.parametrize(
    "query",
    [
        "from=2026-07-26T00:00:00&to=2026-07-27T00:00:00+00:00",
        "from=2026-07-27T00:00:00%2B00:00&to=2026-07-26T00:00:00%2B00:00",
        "from=2026-07-26T00:00:00%2B00:00&to=2026-07-26T00:00:00%2B00:00",
    ],
)
async def test_invalid_usage_range_returns_a_stable_error(
    client, db_session, auth_headers, query: str
):
    response = await client.get(f"{USAGE}?{query}", headers=await auth_headers("hr_lead"))

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "code": "invalid_usage_range",
        "message": "Usage range must be timezone-aware and increasing",
    }


async def test_usage_range_over_ninety_days_is_rejected(client, db_session, auth_headers):
    response = await client.get(
        f"{USAGE}?from=2026-01-01T00:00:00%2B00:00&to=2026-07-01T00:00:00%2B00:00",
        headers=await auth_headers("hr_lead"),
    )

    assert response.status_code == 422
    assert response.json()["detail"] == {
        "code": "usage_range_too_large",
        "message": "Usage range cannot exceed 90 days",
    }


async def test_usage_paginates_newest_first(client, db_session, auth_headers):
    for index in range(3):
        await _seed(db_session, started_at=NOW - timedelta(hours=index + 1))
    await db_session.commit()

    lead = await auth_headers("hr_lead")
    first = (
        await client.get(f"{USAGE}?page=1&page_size=2", headers=lead)
    ).json()
    second = (
        await client.get(f"{USAGE}?page=2&page_size=2", headers=lead)
    ).json()

    assert (first["total"], len(first["items"])) == (3, 2)
    assert (second["page"], len(second["items"])) == (2, 1)
    started = [item["started_at"] for item in first["items"]]
    assert started == sorted(started, reverse=True)


async def test_usage_filters_narrow_the_result_set(client, db_session, auth_headers):
    jd = JD(code="OPS_FILTER", name="Ops", description="", status="active")
    db_session.add(jd)
    await db_session.flush()

    await _seed(
        db_session,
        started_at=NOW - timedelta(hours=1),
        operation="extract",
        attempt_role="fallback",
        requested_model="test-extract",
        actual_model="actual-extract",
        status="invalid_response",
        trace_id="needle-trace",
        jd_id=jd.id,
        ingestion_job_id=None,
    )
    await _seed(db_session, started_at=NOW - timedelta(hours=2))
    await db_session.commit()

    lead = await auth_headers("hr_lead")
    for query in (
        "operation=extract",
        "attempt_role=fallback",
        "requested_model=test-extract",
        "actual_model=actual-extract",
        "status=invalid_response",
        "trace_id=needle-trace",
        "jd_code=OPS_FILTER",
    ):
        body = (await client.get(f"{USAGE}?{query}", headers=lead)).json()
        assert body["total"] == 1, query
        assert body["items"][0]["trace_id"] == "needle-trace", query


async def test_usage_body_carries_no_candidate_content(client, db_session, auth_headers):
    await _seed(db_session, started_at=NOW - timedelta(hours=1))
    await db_session.commit()

    response = await client.get(USAGE, headers=await auth_headers("hr_lead"))
    body = response.json()

    # Structural, not substring-based: the item may carry exactly these keys, so
    # no candidate field can be added later without this failing. (A substring
    # sweep would false-positive on legitimate metadata such as the prompt
    # VERSION identifier "resume_judge_v1".)
    assert set(body["items"][0]) == {
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
    for forbidden in ("private-name", "object/key", "resume_markdown", "evidence_quotes"):
        assert forbidden not in response.text
