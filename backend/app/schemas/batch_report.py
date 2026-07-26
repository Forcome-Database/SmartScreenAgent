from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, PlainSerializer

from backend.app.schemas.quality import _as_json_number

Percentage = Annotated[
    Decimal | None, PlainSerializer(_as_json_number, when_used="json")
]


class RejectionReason(BaseModel):
    reason_type: str
    reason_key: str
    occurrences: int
    affected_scores: int
    percentage: Percentage


class BatchReportFilters(BaseModel):
    batch_id: UUID | None
    jd_code: str | None


class BatchRejectionReport(BaseModel):
    filters: BatchReportFilters
    window_start: datetime
    window_end: datetime
    total_scored: int
    total_rejected: int
    grade_counts: dict[str, int]
    reasons: list[RejectionReason]
    # One candidate can trip several checks, so shares are per-reason, not a
    # partition of the population.
    percentages_may_overlap: bool = True
