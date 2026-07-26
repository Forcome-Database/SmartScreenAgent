from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.database import AsyncSessionLocal
from backend.app.models import AuditLog, LLMUsageAttempt, OperationsReconciliationState
from backend.app.services.llm.pricing import parse_price_book
from backend.app.services.llm.usage import LLMCallContext, UsageRecorder
from backend.app.services.operations.budgets import (
    aggregate_period,
    evaluate_current_budgets,
    period_for,
    reconcile_budget_scope,
    reconcile_budgets,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

# 2026-07-26T05:00Z is 13:00 in Shanghai — mid-day, so the local day/month
# boundaries are unambiguous and the current period is genuinely partial.
NOW = datetime(2026, 7, 26, 5, 0, tzinfo=timezone.utc)

# From TEST_ENV_DEFAULTS: daily 100, monthly 1500, warn ratio 0.80.
DAILY_WARNING_SPEND = Decimal("85")
DAILY_EXCEEDED_SPEND = Decimal("500")


async def _seed_attempt(
    db: AsyncSession,
    *,
    started_at: datetime,
    cost: Decimal | None,
    status: str = "succeeded",
) -> LLMUsageAttempt:
    attempt = LLMUsageAttempt(
        call_group_id=uuid4(),
        trace_id=f"trace-{uuid4()}",
        operation="judge",
        attempt_role="primary",
        requested_model="test-judge",
        actual_model="test-judge",
        prompt_version="resume_judge_v1",
        status=status,
        input_tokens=10 if cost is not None else None,
        output_tokens=5 if cost is not None else None,
        input_price_cny_per_million=Decimal("1.000000"),
        output_price_cny_per_million=Decimal("2.000000"),
        estimated_cost_cny=cost,
        latency_ms=12,
        error_code=None,
        started_at=started_at,
        finished_at=started_at + timedelta(seconds=1),
    )
    db.add(attempt)
    await db.flush()
    return attempt


async def _audit_rows(db: AsyncSession, event_type: str) -> list[AuditLog]:
    rows = (
        await db.execute(
            select(AuditLog).where(AuditLog.event_type == event_type).order_by(AuditLog.id)
        )
    ).scalars()
    return list(rows)


async def _cursor(db: AsyncSession, key: str) -> OperationsReconciliationState | None:
    return await db.get(OperationsReconciliationState, key, populate_existing=True)


async def test_aggregate_period_sums_costs_and_counts_unknowns(db_session) -> None:
    daily = period_for("daily", NOW)
    await _seed_attempt(db_session, started_at=NOW, cost=Decimal("1.5"))
    # Failed attempts still cost money and must be summed.
    await _seed_attempt(
        db_session, started_at=NOW, cost=Decimal("0.25"), status="unavailable"
    )
    # Unknown usage contributes to the separate count, not the sum.
    await _seed_attempt(db_session, started_at=NOW, cost=None, status="invalid_response")
    # Outside the half-open window on both ends.
    await _seed_attempt(
        db_session, started_at=daily.start - timedelta(seconds=1), cost=Decimal("99")
    )
    await _seed_attempt(db_session, started_at=daily.end, cost=Decimal("99"))
    await db_session.commit()

    spend, unknown = await aggregate_period(db_session, daily.start, daily.end)

    assert spend == Decimal("1.75")
    assert unknown == 1


async def test_direct_jump_to_exceeded_emits_both_thresholds(db_session) -> None:
    await _seed_attempt(db_session, started_at=NOW, cost=DAILY_EXCEEDED_SPEND)
    await db_session.commit()

    evaluations = await evaluate_current_budgets(db_session, now=NOW)
    await db_session.commit()

    daily = next(e for e in evaluations if e.scope == "daily")
    assert daily.state == "exceeded"
    assert len(await _audit_rows(db_session, "llm_budget_warning")) == 1
    assert len(await _audit_rows(db_session, "llm_budget_exceeded")) == 1


async def test_audit_payload_is_metadata_only_and_omits_the_dedupe_key(db_session) -> None:
    await _seed_attempt(db_session, started_at=NOW, cost=DAILY_WARNING_SPEND)
    await db_session.commit()

    await evaluate_current_budgets(db_session, now=NOW)
    await db_session.commit()

    (row,) = await _audit_rows(db_session, "llm_budget_warning")
    assert row.actor == "system:wp7"
    assert set(row.payload) == {
        "scope",
        "period_start",
        "period_end",
        "threshold",
        "budget_cny",
        "spend_cny",
        "unknown_cost_count",
    }
    daily = period_for("daily", NOW)
    composed_key = f"daily:{daily.start.isoformat()}:warning"
    assert composed_key not in str(row.payload)
    # No candidate-identifying or free-text fields leak through the payload.
    assert row.target_type is None


async def test_concurrent_evaluations_emit_exactly_one_audit_row(db_session) -> None:
    await _seed_attempt(db_session, started_at=NOW, cost=DAILY_WARNING_SPEND)
    await db_session.commit()

    async def evaluate_once() -> None:
        async with AsyncSessionLocal() as session:
            await evaluate_current_budgets(session, now=NOW)
            await session.commit()

    await asyncio.gather(evaluate_once(), evaluate_once())

    assert len(await _audit_rows(db_session, "llm_budget_warning")) == 1


async def test_backlog_periods_are_processed_in_order_and_cursor_advances(db_session) -> None:
    today = period_for("daily", NOW)
    day_minus_1 = period_for("daily", today.start - timedelta(hours=1))
    day_minus_2 = period_for("daily", day_minus_1.start - timedelta(hours=1))

    await _seed_attempt(db_session, started_at=day_minus_2.start, cost=DAILY_WARNING_SPEND)
    await _seed_attempt(db_session, started_at=day_minus_1.start, cost=DAILY_EXCEEDED_SPEND)
    await db_session.commit()

    processed = await reconcile_budget_scope(
        db_session, scope="daily", now=NOW, max_periods=31
    )
    await db_session.commit()

    assert processed == 2
    warnings = await _audit_rows(db_session, "llm_budget_warning")
    exceeded = await _audit_rows(db_session, "llm_budget_exceeded")
    # Oldest period first, and only the over-budget day escalates.
    assert [row.payload["period_start"] for row in warnings] == [
        day_minus_2.start.isoformat(),
        day_minus_1.start.isoformat(),
    ]
    assert [row.payload["period_start"] for row in exceeded] == [day_minus_1.start.isoformat()]

    cursor = await _cursor(db_session, "daily")
    assert cursor is not None
    # Advanced past every COMPLETE period, stopping at the still-open one.
    assert cursor.next_period_start == today.start


async def test_max_periods_leaves_backlog_but_still_evaluates_current_period(
    db_session,
) -> None:
    today = period_for("daily", NOW)
    day_minus_1 = period_for("daily", today.start - timedelta(hours=1))
    day_minus_2 = period_for("daily", day_minus_1.start - timedelta(hours=1))

    await _seed_attempt(db_session, started_at=day_minus_2.start, cost=DAILY_WARNING_SPEND)
    await _seed_attempt(db_session, started_at=day_minus_1.start, cost=DAILY_WARNING_SPEND)
    await _seed_attempt(db_session, started_at=NOW, cost=DAILY_WARNING_SPEND)
    await db_session.commit()

    report = await reconcile_budgets(db_session, now=NOW, max_periods=1)
    await db_session.commit()

    assert report.processed["daily"] == 1
    cursor = await _cursor(db_session, "daily")
    assert cursor is not None
    assert cursor.next_period_start == day_minus_1.start

    warnings = await _audit_rows(db_session, "llm_budget_warning")
    emitted = {row.payload["period_start"] for row in warnings}
    # The backlog is drained one period per run, but today is never starved.
    assert day_minus_2.start.isoformat() in emitted
    assert today.start.isoformat() in emitted
    assert day_minus_1.start.isoformat() not in emitted


async def test_first_use_initializes_cursor_from_earliest_ledger_period(db_session) -> None:
    oldest = NOW - timedelta(days=3)
    await _seed_attempt(db_session, started_at=oldest, cost=Decimal("1"))
    await db_session.commit()

    await reconcile_budget_scope(db_session, scope="daily", now=NOW, max_periods=31)
    await db_session.commit()

    cursor = await _cursor(db_session, "daily")
    assert cursor is not None
    # Started at the oldest ledger day, then drained forward to the open period.
    assert cursor.next_period_start == period_for("daily", NOW).start


async def test_first_use_on_empty_ledger_starts_at_the_current_period(db_session) -> None:
    await reconcile_budget_scope(db_session, scope="monthly", now=NOW, max_periods=31)
    await db_session.commit()

    cursor = await _cursor(db_session, "monthly")
    assert cursor is not None
    assert cursor.next_period_start == period_for("monthly", NOW).start


async def test_concurrent_first_use_creates_exactly_one_cursor_row(db_session) -> None:
    await _seed_attempt(db_session, started_at=NOW - timedelta(days=2), cost=Decimal("1"))
    await db_session.commit()

    async def reconcile_once() -> None:
        async with AsyncSessionLocal() as session:
            await reconcile_budget_scope(session, scope="daily", now=NOW, max_periods=31)
            await session.commit()

    await asyncio.gather(reconcile_once(), reconcile_once())

    total = (
        await db_session.execute(
            select(func.count())
            .select_from(OperationsReconciliationState)
            .where(OperationsReconciliationState.key == "daily")
        )
    ).scalar_one()
    assert total == 1


async def test_enqueue_failure_does_not_change_finalization_or_ledger_state(
    db_session,
) -> None:
    def broken_enqueue(_attempt_id: int) -> None:
        raise RuntimeError("broker unavailable")

    recorder = UsageRecorder(
        prices=parse_price_book('{"test-judge":{"input":1,"output":2}}'),
        enqueue=broken_enqueue,
    )
    handle = await recorder.begin(
        context=LLMCallContext(operation="judge", call_group_id=uuid4(), trace_id="t-enqueue"),
        requested_model="test-judge",
        attempt_role="primary",
        prompt_version="resume_judge_v1",
    )

    # A paid call must never be reported as unaccounted because the broker died.
    assert await recorder.finalize(
        handle,
        status="succeeded",
        actual_model="test-judge",
        input_tokens=10,
        output_tokens=5,
        latency_ms=12,
        error_code=None,
    )

    row = await db_session.get(LLMUsageAttempt, handle.attempt_id, populate_existing=True)
    assert row is not None
    assert row.status == "succeeded"
    assert row.finished_at is not None
    assert row.estimated_cost_cny == Decimal("0.000020000000")
