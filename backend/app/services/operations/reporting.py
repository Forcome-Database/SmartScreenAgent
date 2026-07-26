from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import get_settings
from backend.app.models import JD, LLMUsageAttempt
from backend.app.schemas.operations import (
    BudgetSnapshot,
    OperationsBreakdown,
    OperationsDelta,
    OperationsSeriesPoint,
    OperationsSummary,
    OperationsTotals,
    UsageItem,
    UsagePage,
)
from backend.app.services.operations.budgets import (
    SCOPES,
    Scope,
    aggregate_period,
    budget_state,
    local_periods,
    period_for,
)
from backend.app.services.read.pagination import Page

FAILED_STATUSES = ("unavailable", "invalid_response", "configuration_error")
UNKNOWN_KEY = "(unknown)"
LOCAL_ZONE_NAME = "Asia/Shanghai"

WindowName = Literal["today", "7d", "30d"]

# Kept in the same order they are offered in the UI.
WINDOW_NAMES: tuple[WindowName, ...] = ("today", "7d", "30d")
_WINDOW_DAYS: dict[str, int] = {"7d": 7, "30d": 30}

MAX_USAGE_RANGE = timedelta(days=90)


class InvalidOperationsWindow(ValueError):
    """The requested summary window is not one of `WINDOW_NAMES`."""


class InvalidUsageRange(ValueError):
    """Usage bounds are naive, or do not strictly increase."""


class UsageRangeTooLarge(ValueError):
    """Usage bounds span more than `MAX_USAGE_RANGE`."""


@dataclass(frozen=True)
class ReportWindow:
    """Two equal-length half-open UTC ranges: the current one and its predecessor."""

    window: WindowName
    current_start: datetime
    current_end: datetime
    previous_start: datetime
    previous_end: datetime


def _require_aware(moment: datetime, message: str) -> None:
    if moment.tzinfo is None or moment.tzinfo.utcoffset(moment) is None:
        raise ValueError(message)


def resolve_window(window: str, now: datetime) -> ReportWindow:
    if window not in WINDOW_NAMES:
        raise InvalidOperationsWindow(f"unsupported operations window: {window!r}")
    _require_aware(now, "now must be timezone-aware")

    if window == "today":
        current_start = period_for("daily", now).start
        # Compare like with like: the same elapsed slice of the previous local
        # day, not the whole day, which would always dwarf a partial one.
        elapsed = now - current_start
        previous_start = period_for("daily", current_start - timedelta(hours=1)).start
        previous_end = previous_start + elapsed
    else:
        span = timedelta(days=_WINDOW_DAYS[window])
        current_start = now - span
        previous_start = current_start - span
        previous_end = current_start

    return ReportWindow(
        window=window,  # type: ignore[arg-type]
        current_start=current_start,
        current_end=now,
        previous_start=previous_start,
        previous_end=previous_end,
    )


def validate_usage_range(start: datetime, end: datetime) -> None:
    for bound in (start, end):
        if bound.tzinfo is None or bound.tzinfo.utcoffset(bound) is None:
            raise InvalidUsageRange("usage bounds must be timezone-aware")
    if start >= end:
        raise InvalidUsageRange("usage range must be increasing")
    if end - start > MAX_USAGE_RANGE:
        raise UsageRangeTooLarge("usage range is too large")


def _in_window(start: datetime, end: datetime) -> list[Any]:
    return [LLMUsageAttempt.started_at >= start, LLMUsageAttempt.started_at < end]


def _known_usage() -> Any:
    return LLMUsageAttempt.input_tokens.isnot(None) & LLMUsageAttempt.output_tokens.isnot(
        None
    )


def _local_date() -> Any:
    return func.date(func.timezone(LOCAL_ZONE_NAME, LLMUsageAttempt.started_at))


def _measurable_latency() -> Any:
    """Only terminal attempts with a recorded latency describe real service time."""
    return (LLMUsageAttempt.status != "pending") & LLMUsageAttempt.latency_ms.isnot(None)


async def _totals(db: AsyncSession, start: datetime, end: datetime) -> OperationsTotals:
    latency = func.percentile_cont
    row = (
        await db.execute(
            select(
                func.count(),
                func.coalesce(func.sum(LLMUsageAttempt.estimated_cost_cny), 0),
                func.coalesce(
                    func.sum(
                        LLMUsageAttempt.input_tokens + LLMUsageAttempt.output_tokens
                    ).filter(_known_usage()),
                    0,
                ),
                func.count().filter(~_known_usage()),
                func.count().filter(LLMUsageAttempt.status == "succeeded"),
                func.count().filter(LLMUsageAttempt.status.in_(FAILED_STATUSES)),
                func.count().filter(LLMUsageAttempt.status == "abandoned"),
                func.count().filter(LLMUsageAttempt.status == "pending"),
                latency(0.50)
                .within_group(LLMUsageAttempt.latency_ms)
                .filter(_measurable_latency()),
                latency(0.95)
                .within_group(LLMUsageAttempt.latency_ms)
                .filter(_measurable_latency()),
                func.max(LLMUsageAttempt.finished_at),
            ).where(*_in_window(start, end))
        )
    ).one()

    return OperationsTotals(
        attempt_count=int(row[0]),
        known_cost_cny=Decimal(str(row[1])),
        known_token_total=int(row[2]),
        unknown_usage_count=int(row[3]),
        succeeded_count=int(row[4]),
        failed_count=int(row[5]),
        abandoned_count=int(row[6]),
        pending_count=int(row[7]),
        p50_latency_ms=None if row[8] is None else Decimal(str(row[8])),
        p95_latency_ms=None if row[9] is None else Decimal(str(row[9])),
        last_completed_at=row[10],
    )


def _delta(current: Decimal, previous: Decimal) -> OperationsDelta:
    absolute = current - previous
    # A zero baseline has no meaningful growth rate; report the absolute move only.
    percentage = None if previous == 0 else (absolute / previous) * 100
    return OperationsDelta(absolute=absolute, percentage=percentage)


async def _series(
    db: AsyncSession, start: datetime, end: datetime
) -> list[OperationsSeriesPoint]:
    # One expression object, reused in all three clauses: rebuilding it would
    # emit a distinct bind parameter each time and PostgreSQL would refuse to
    # match the GROUP BY expression against the selected column.
    local_date = _local_date()
    rows = (
        await db.execute(
            select(
                local_date.label("local_date"),
                func.count(),
                func.coalesce(func.sum(LLMUsageAttempt.estimated_cost_cny), 0),
                func.count().filter(~_known_usage()),
            )
            .where(*_in_window(start, end))
            .group_by(local_date)
            .order_by(local_date)
        )
    ).all()
    return [
        OperationsSeriesPoint(
            local_date=row[0],
            attempt_count=int(row[1]),
            known_cost_cny=Decimal(str(row[2])),
            unknown_usage_count=int(row[3]),
        )
        for row in rows
    ]


async def _breakdown(
    db: AsyncSession, start: datetime, end: datetime, column: Any
) -> list[OperationsBreakdown]:
    key = func.coalesce(column, UNKNOWN_KEY)
    rows = (
        await db.execute(
            select(
                key.label("key"),
                func.count(),
                func.coalesce(func.sum(LLMUsageAttempt.estimated_cost_cny), 0),
                func.count().filter(~_known_usage()),
            )
            .where(*_in_window(start, end))
            .group_by(key)
            .order_by(func.count().desc(), key)
        )
    ).all()
    return [
        OperationsBreakdown(
            key=str(row[0]),
            attempt_count=int(row[1]),
            known_cost_cny=Decimal(str(row[2])),
            unknown_usage_count=int(row[3]),
        )
        for row in rows
    ]


async def _budget_snapshots(db: AsyncSession, now: datetime) -> list[BudgetSnapshot]:
    """Read-only budget view. Never emits audit events — that is the sweeper's job."""
    settings = get_settings()
    snapshots: list[BudgetSnapshot] = []
    for period in local_periods(now):
        scope: Scope = period.scope
        budget = Decimal(
            str(
                settings.DAILY_LLM_BUDGET_CNY
                if scope == "daily"
                else settings.MONTHLY_LLM_BUDGET_CNY
            )
        )
        spend, unknown = await aggregate_period(db, period.start, period.end)
        snapshots.append(
            BudgetSnapshot(
                scope=scope,
                period_start=period.start,
                period_end=period.end,
                budget_cny=budget,
                spend_cny=spend,
                ratio=None if budget == 0 else spend / budget,
                unknown_cost_count=unknown,
                state=budget_state(spend, budget, settings.LLM_BUDGET_WARN_RATIO),
            )
        )
    return sorted(snapshots, key=lambda item: SCOPES.index(item.scope))


async def summarize(db: AsyncSession, *, window: str, now: datetime) -> OperationsSummary:
    resolved = resolve_window(window, now)
    current = await _totals(db, resolved.current_start, resolved.current_end)
    previous = await _totals(db, resolved.previous_start, resolved.previous_end)

    return OperationsSummary(
        window=resolved.window,
        current_start=resolved.current_start,
        current_end=resolved.current_end,
        previous_start=resolved.previous_start,
        previous_end=resolved.previous_end,
        current=current,
        previous=previous,
        cost_delta=_delta(current.known_cost_cny, previous.known_cost_cny),
        attempt_delta=_delta(
            Decimal(current.attempt_count), Decimal(previous.attempt_count)
        ),
        daily_series=await _series(db, resolved.current_start, resolved.current_end),
        by_operation=await _breakdown(
            db, resolved.current_start, resolved.current_end, LLMUsageAttempt.operation
        ),
        by_requested_model=await _breakdown(
            db,
            resolved.current_start,
            resolved.current_end,
            LLMUsageAttempt.requested_model,
        ),
        by_actual_model=await _breakdown(
            db, resolved.current_start, resolved.current_end, LLMUsageAttempt.actual_model
        ),
        by_outcome=await _breakdown(
            db, resolved.current_start, resolved.current_end, LLMUsageAttempt.status
        ),
        by_attempt_role=await _breakdown(
            db, resolved.current_start, resolved.current_end, LLMUsageAttempt.attempt_role
        ),
        budgets=await _budget_snapshots(db, now),
    )


@dataclass(frozen=True)
class UsageFilters:
    operation: str | None = None
    attempt_role: str | None = None
    requested_model: str | None = None
    actual_model: str | None = None
    status: str | None = None
    trace_id: str | None = None
    ingestion_job_id: int | None = None
    score_id: int | None = None
    jd_code: str | None = None


def _apply_filters(statement: Select[Any], filters: UsageFilters) -> Select[Any]:
    simple = (
        (LLMUsageAttempt.operation, filters.operation),
        (LLMUsageAttempt.attempt_role, filters.attempt_role),
        (LLMUsageAttempt.requested_model, filters.requested_model),
        (LLMUsageAttempt.actual_model, filters.actual_model),
        (LLMUsageAttempt.status, filters.status),
        (LLMUsageAttempt.trace_id, filters.trace_id),
        (LLMUsageAttempt.ingestion_job_id, filters.ingestion_job_id),
        (LLMUsageAttempt.score_id, filters.score_id),
    )
    for column, value in simple:
        if value is not None:
            statement = statement.where(column == value)
    if filters.jd_code is not None:
        # JD is joined only to resolve the code; no JD content reaches the response.
        statement = statement.join(JD, JD.id == LLMUsageAttempt.jd_id).where(
            JD.code == filters.jd_code
        )
    return statement


async def list_usage(
    db: AsyncSession,
    *,
    start: datetime,
    end: datetime,
    page: Page,
    filters: UsageFilters,
) -> UsagePage:
    validate_usage_range(start, end)

    total = (
        await db.execute(
            _apply_filters(
                select(func.count()).select_from(LLMUsageAttempt).where(*_in_window(start, end)),
                filters,
            )
        )
    ).scalar_one()

    rows = (
        await db.execute(
            _apply_filters(
                select(LLMUsageAttempt).where(*_in_window(start, end)), filters
            )
            .order_by(LLMUsageAttempt.started_at.desc(), LLMUsageAttempt.id.desc())
            .offset(page.offset)
            .limit(page.page_size)
        )
    ).scalars()

    return UsagePage(
        items=[
            UsageItem(
                id=row.id,
                call_group_id=row.call_group_id,
                trace_id=row.trace_id,
                ingestion_job_id=row.ingestion_job_id,
                score_id=row.score_id,
                jd_id=row.jd_id,
                rule_version_id=row.rule_version_id,
                operation=row.operation,
                attempt_role=row.attempt_role,
                requested_model=row.requested_model,
                actual_model=row.actual_model,
                prompt_version=row.prompt_version,
                status=row.status,
                input_tokens=row.input_tokens,
                output_tokens=row.output_tokens,
                input_price_cny_per_million=row.input_price_cny_per_million,
                output_price_cny_per_million=row.output_price_cny_per_million,
                estimated_cost_cny=row.estimated_cost_cny,
                latency_ms=row.latency_ms,
                error_code=row.error_code,
                started_at=row.started_at,
                finished_at=row.finished_at,
            )
            for row in rows
        ],
        page=page.page,
        page_size=page.page_size,
        total=int(total),
    )
