from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

# Every model here is metadata-only. No prompt, response, evidence, reasoning,
# candidate field, ciphertext, or object key may ever gain a home in this file.


class OperationsTotals(BaseModel):
    attempt_count: int
    known_cost_cny: Decimal
    known_token_total: int
    unknown_usage_count: int
    succeeded_count: int
    failed_count: int
    abandoned_count: int
    pending_count: int
    p50_latency_ms: Decimal | None
    p95_latency_ms: Decimal | None
    last_completed_at: datetime | None


class OperationsDelta(BaseModel):
    absolute: Decimal | None
    percentage: Decimal | None


class OperationsSeriesPoint(BaseModel):
    local_date: date
    attempt_count: int
    known_cost_cny: Decimal
    unknown_usage_count: int


class OperationsBreakdown(BaseModel):
    key: str
    attempt_count: int
    known_cost_cny: Decimal
    unknown_usage_count: int


class BudgetSnapshot(BaseModel):
    scope: Literal["daily", "monthly"]
    period_start: datetime
    period_end: datetime
    budget_cny: Decimal
    spend_cny: Decimal
    ratio: Decimal | None
    unknown_cost_count: int
    state: Literal["normal", "warning", "exceeded"]


class OperationsSummary(BaseModel):
    window: Literal["today", "7d", "30d"]
    current_start: datetime
    current_end: datetime
    previous_start: datetime
    previous_end: datetime
    current: OperationsTotals
    previous: OperationsTotals
    cost_delta: OperationsDelta
    attempt_delta: OperationsDelta
    daily_series: list[OperationsSeriesPoint]
    by_operation: list[OperationsBreakdown]
    by_requested_model: list[OperationsBreakdown]
    by_actual_model: list[OperationsBreakdown]
    by_outcome: list[OperationsBreakdown]
    by_attempt_role: list[OperationsBreakdown]
    budgets: list[BudgetSnapshot]


class UsageItem(BaseModel):
    id: int
    call_group_id: UUID
    trace_id: str | None
    ingestion_job_id: int | None
    score_id: int | None
    jd_id: int | None
    rule_version_id: int | None
    operation: str
    attempt_role: str
    requested_model: str
    actual_model: str | None
    prompt_version: str
    status: str
    input_tokens: int | None
    output_tokens: int | None
    input_price_cny_per_million: Decimal
    output_price_cny_per_million: Decimal
    estimated_cost_cny: Decimal | None
    latency_ms: int | None
    error_code: str | None
    started_at: datetime
    finished_at: datetime | None


class UsagePage(BaseModel):
    items: list[UsageItem]
    page: int
    page_size: int
    total: int
