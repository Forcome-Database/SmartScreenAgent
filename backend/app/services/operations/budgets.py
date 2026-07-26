from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import Settings, get_settings
from backend.app.models import AuditLog, LLMUsageAttempt, OperationsReconciliationState

logger = logging.getLogger(__name__)

LOCAL_ZONE = ZoneInfo("Asia/Shanghai")

ALERT_ACTOR = "system:wp7"

Scope = Literal["daily", "monthly"]
BudgetState = Literal["normal", "warning", "exceeded"]
Threshold = Literal["warning", "exceeded"]

SCOPES: tuple[Scope, ...] = ("daily", "monthly")


@dataclass(frozen=True)
class Period:
    """A half-open `[start, end)` window in UTC, derived from local calendar boundaries."""

    scope: Scope
    start: datetime
    end: datetime


def _local_midnight(moment: datetime) -> datetime:
    """Local midnight of the calendar day containing `moment`.

    Rebuilt from the date rather than by adding a `timedelta`, so the offset is
    resolved by the zone for that specific day instead of being carried over.
    """
    return datetime(moment.year, moment.month, moment.day, tzinfo=LOCAL_ZONE)


def local_periods(now: datetime) -> tuple[Period, Period]:
    """Return the `(daily, monthly)` UTC periods containing `now` in `Asia/Shanghai`."""
    if now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
        raise ValueError("now must be timezone-aware")

    local = now.astimezone(LOCAL_ZONE)
    day_start = _local_midnight(local)
    day_end = _local_midnight(day_start + timedelta(days=1))
    month_start = _local_midnight(local.replace(day=1))
    month_end = _local_midnight((month_start + timedelta(days=32)).replace(day=1))

    return (
        Period("daily", day_start.astimezone(UTC), day_end.astimezone(UTC)),
        Period("monthly", month_start.astimezone(UTC), month_end.astimezone(UTC)),
    )


def budget_state(spend: Decimal, budget: Decimal, warn_ratio: float) -> BudgetState:
    """Classify `spend` against `budget`. Informational only — never gates a call."""
    if budget <= 0:
        # A zero budget cannot be divided into ratios: any spend at all is over it.
        return "exceeded" if spend > 0 else "normal"
    if spend >= budget:
        return "exceeded"
    if spend >= budget * Decimal(str(warn_ratio)):
        return "warning"
    return "normal"


def thresholds_crossed(
    spend: Decimal, budget: Decimal, warn_ratio: float
) -> tuple[Threshold, ...]:
    """Thresholds reached at this spend.

    A direct jump past both thresholds reports both, because each one owns an
    independent dedupe key and must still produce its own audit event.
    """
    state = budget_state(spend, budget, warn_ratio)
    if state == "exceeded":
        return ("warning", "exceeded")
    if state == "warning":
        return ("warning",)
    return ()


@dataclass(frozen=True)
class BudgetEvaluation:
    scope: Scope
    period_start: datetime
    period_end: datetime
    budget: Decimal
    spend: Decimal
    unknown_cost_count: int
    state: BudgetState


@dataclass(frozen=True)
class ReconcileReport:
    processed: dict[Scope, int]
    current: list[BudgetEvaluation]


def period_for(scope: Scope, moment: datetime) -> Period:
    daily, monthly = local_periods(moment)
    return daily if scope == "daily" else monthly


def _budget_for(scope: Scope, settings: Settings) -> Decimal:
    raw = (
        settings.DAILY_LLM_BUDGET_CNY
        if scope == "daily"
        else settings.MONTHLY_LLM_BUDGET_CNY
    )
    return Decimal(str(raw))


async def aggregate_period(
    db: AsyncSession, start: datetime, end: datetime
) -> tuple[Decimal, int]:
    """Total known cost and unknown-cost attempt count in the half-open window.

    Every attempt is counted regardless of terminal outcome — a failed call that
    burned tokens still costs money.
    """
    spend, unknown = (
        await db.execute(
            select(
                func.coalesce(func.sum(LLMUsageAttempt.estimated_cost_cny), 0),
                func.count().filter(LLMUsageAttempt.estimated_cost_cny.is_(None)),
            ).where(
                LLMUsageAttempt.started_at >= start,
                LLMUsageAttempt.started_at < end,
            )
        )
    ).one()
    return Decimal(str(spend)), int(unknown)


async def _insert_threshold_once(
    db: AsyncSession,
    *,
    period: Period,
    threshold: Threshold,
    budget: Decimal,
    spend: Decimal,
    unknown_cost_count: int,
) -> bool:
    """Insert one threshold audit event, at most once per period and threshold.

    A transaction-scoped advisory lock on the dedupe key serializes the
    check-then-insert, so concurrent evaluators cannot both observe "absent".
    The composed key is only a lock input — it is never persisted.
    """
    dedupe_key = f"{period.scope}:{period.start.isoformat()}:{threshold}"
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))").bindparams(
            key=dedupe_key
        )
    )

    event_type = f"llm_budget_{threshold}"
    already_emitted = (
        await db.execute(
            select(AuditLog.id)
            .where(
                AuditLog.event_type == event_type,
                AuditLog.payload["scope"].astext == period.scope,
                AuditLog.payload["period_start"].astext == period.start.isoformat(),
                AuditLog.payload["threshold"].astext == threshold,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if already_emitted is not None:
        return False

    db.add(
        AuditLog(
            event_type=event_type,
            actor=ALERT_ACTOR,
            payload={
                "scope": period.scope,
                "period_start": period.start.isoformat(),
                "period_end": period.end.isoformat(),
                "threshold": threshold,
                "budget_cny": str(budget),
                "spend_cny": str(spend),
                "unknown_cost_count": unknown_cost_count,
            },
        )
    )
    await db.flush()
    return True


async def _evaluate_period(
    db: AsyncSession, period: Period, *, settings: Settings
) -> BudgetEvaluation:
    budget = _budget_for(period.scope, settings)
    spend, unknown_cost_count = await aggregate_period(db, period.start, period.end)
    warn_ratio = settings.LLM_BUDGET_WARN_RATIO

    for threshold in thresholds_crossed(spend, budget, warn_ratio):
        await _insert_threshold_once(
            db,
            period=period,
            threshold=threshold,
            budget=budget,
            spend=spend,
            unknown_cost_count=unknown_cost_count,
        )

    return BudgetEvaluation(
        scope=period.scope,
        period_start=period.start,
        period_end=period.end,
        budget=budget,
        spend=spend,
        unknown_cost_count=unknown_cost_count,
        state=budget_state(spend, budget, warn_ratio),
    )


async def evaluate_current_budgets(
    db: AsyncSession, *, now: datetime
) -> list[BudgetEvaluation]:
    """Evaluate the daily and monthly periods containing `now`."""
    settings = get_settings()
    return [
        await _evaluate_period(db, period, settings=settings)
        for period in local_periods(now)
    ]


async def _initial_cursor_start(db: AsyncSession, scope: Scope, now: datetime) -> datetime:
    earliest = (
        await db.execute(select(func.min(LLMUsageAttempt.started_at)))
    ).scalar_one_or_none()
    reference = earliest if earliest is not None else now
    return period_for(scope, reference).start


async def reconcile_budget_scope(
    db: AsyncSession, *, scope: Scope, now: datetime, max_periods: int
) -> int:
    """Drain complete periods for one scope, oldest first, bounded per run.

    The cursor advances only alongside the audit rows for the period it just
    finished, in the same transaction, so a crash re-does a period rather than
    skipping it.
    """
    settings = get_settings()

    await db.execute(
        pg_insert(OperationsReconciliationState)
        .values(
            key=scope,
            next_period_start=await _initial_cursor_start(db, scope, now),
            updated_at=datetime.now(UTC),
        )
        .on_conflict_do_nothing(index_elements=["key"])
    )
    cursor = (
        await db.execute(
            select(OperationsReconciliationState)
            .where(OperationsReconciliationState.key == scope)
            .with_for_update()
        )
    ).scalar_one()

    processed = 0
    period = period_for(scope, cursor.next_period_start)
    while processed < max_periods and period.end <= now:
        await _evaluate_period(db, period, settings=settings)
        period = period_for(scope, period.end)
        cursor.next_period_start = period.start
        cursor.updated_at = datetime.now(UTC)
        processed += 1
        await db.flush()

    return processed


async def reconcile_budgets(
    db: AsyncSession, *, now: datetime, max_periods: int
) -> ReconcileReport:
    """Drain each scope's backlog, then evaluate both current partial periods."""
    processed = {
        scope: await reconcile_budget_scope(
            db, scope=scope, now=now, max_periods=max_periods
        )
        for scope in SCOPES
    }
    return ReconcileReport(
        processed=processed,
        current=await evaluate_current_budgets(db, now=now),
    )
