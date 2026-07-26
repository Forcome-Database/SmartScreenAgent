from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.database import get_db
from backend.app.deps import require_roles
from backend.app.models import User
from backend.app.schemas.operations import OperationsSummary, UsagePage
from backend.app.services.operations.reporting import (
    InvalidOperationsWindow,
    InvalidUsageRange,
    UsageFilters,
    UsageRangeTooLarge,
    list_usage,
    summarize,
)
from backend.app.services.read.pagination import Page, page_params

router = APIRouter(prefix="/api/v1", tags=["operations"])
OPERATIONS_ROLES = ("hr_lead", "admin")

DEFAULT_USAGE_SPAN = timedelta(days=7)

_INVALID_RANGE = {
    "code": "invalid_usage_range",
    "message": "Usage range must be timezone-aware and increasing",
}
_RANGE_TOO_LARGE = {
    "code": "usage_range_too_large",
    "message": "Usage range cannot exceed 90 days",
}


def _parse_bound(raw: str | None, default: datetime) -> datetime:
    if raw is None:
        return default
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=_INVALID_RANGE) from exc
    if parsed.tzinfo is None:
        raise HTTPException(status_code=422, detail=_INVALID_RANGE)
    return parsed


@router.get("/operations/summary", response_model=OperationsSummary)
async def operations_summary(
    window: str = Query("today"),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_roles(*OPERATIONS_ROLES)),
) -> OperationsSummary:
    try:
        return await summarize(db, window=window, now=datetime.now(UTC))
    except InvalidOperationsWindow as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_operations_window",
                "message": "Unsupported operations window",
            },
        ) from exc


@router.get("/operations/usage", response_model=UsagePage)
async def operations_usage(
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    operation: str | None = Query(None),
    attempt_role: str | None = Query(None),
    requested_model: str | None = Query(None),
    actual_model: str | None = Query(None),
    status: str | None = Query(None),
    trace_id: str | None = Query(None),
    ingestion_job_id: int | None = Query(None),
    score_id: int | None = Query(None),
    jd_code: str | None = Query(None),
    page: Page = Depends(page_params),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_roles(*OPERATIONS_ROLES)),
) -> UsagePage:
    now = datetime.now(UTC)
    start = _parse_bound(from_, now - DEFAULT_USAGE_SPAN)
    end = _parse_bound(to, now)

    try:
        return await list_usage(
            db,
            start=start,
            end=end,
            page=page,
            filters=UsageFilters(
                operation=operation,
                attempt_role=attempt_role,
                requested_model=requested_model,
                actual_model=actual_model,
                status=status,
                trace_id=trace_id,
                ingestion_job_id=ingestion_job_id,
                score_id=score_id,
                jd_code=jd_code,
            ),
        )
    except InvalidUsageRange as exc:
        raise HTTPException(status_code=422, detail=_INVALID_RANGE) from exc
    except UsageRangeTooLarge as exc:
        raise HTTPException(status_code=422, detail=_RANGE_TOO_LARGE) from exc
