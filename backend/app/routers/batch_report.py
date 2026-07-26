from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.database import get_db
from backend.app.deps import require_roles
from backend.app.models import JD, IngestionJob, RuleVersion, Score, User
from backend.app.schemas.batch_report import BatchRejectionReport
from backend.app.services.quality.batch import (
    RejectedScore,
    aggregate_rejection_reasons,
)

router = APIRouter(prefix="/api/v1", tags=["batch-report"])
READ_ROLES = ("hr", "hr_lead", "admin")

DEFAULT_BATCH_WINDOW = timedelta(days=30)
MAX_BATCH_WINDOW = timedelta(days=90)

_FILTER_REQUIRED = {
    "code": "batch_filter_required",
    "message": "Provide a batch id, a JD code, or an explicit time window",
}
_INVALID_WINDOW = {
    "code": "invalid_batch_window",
    "message": "Batch window must be timezone-aware and increasing",
}
_WINDOW_TOO_LARGE = {
    "code": "batch_window_too_large",
    "message": "Batch window cannot exceed 90 days",
}


def _parse(raw: str | None) -> datetime | None:
    if raw is None:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=_INVALID_WINDOW) from exc
    if parsed.tzinfo is None:
        raise HTTPException(status_code=422, detail=_INVALID_WINDOW)
    return parsed


@router.get("/reports/batch", response_model=BatchRejectionReport)
async def batch_rejection_report(
    batch_id: UUID | None = Query(None),
    jd_code: str | None = Query(None),
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_roles(*READ_ROLES)),
) -> BatchRejectionReport:
    start = _parse(from_)
    end = _parse(to)
    explicit_window = start is not None and end is not None
    if batch_id is None and jd_code is None and not explicit_window:
        raise HTTPException(status_code=422, detail=_FILTER_REQUIRED)

    now = datetime.now(timezone.utc)
    resolved_end = end or now
    resolved_start = start or (resolved_end - DEFAULT_BATCH_WINDOW)
    if resolved_start >= resolved_end:
        raise HTTPException(status_code=422, detail=_INVALID_WINDOW)
    if resolved_end - resolved_start > MAX_BATCH_WINDOW:
        raise HTTPException(status_code=422, detail=_WINDOW_TOO_LARGE)

    statement = (
        select(Score, RuleVersion.schema_json)
        .join(RuleVersion, RuleVersion.id == Score.rule_version_id)
        .where(Score.created_at >= resolved_start, Score.created_at < resolved_end)
    )
    if jd_code is not None:
        statement = statement.join(JD, JD.id == Score.jd_id).where(JD.code == jd_code)
    if batch_id is not None:
        statement = statement.where(
            Score.id.in_(
                select(IngestionJob.score_id).where(IngestionJob.batch_id == batch_id)
            )
        )

    rows = (await db.execute(statement)).all()

    grade_counts: dict[str, int] = {}
    rejected: list[RejectedScore] = []
    weights: dict[tuple[int, str], Decimal] = {}
    for score, schema_json in rows:
        grade_counts[score.grade] = grade_counts.get(score.grade, 0) + 1
        for key in ("rule_dimensions", "judge_dimensions"):
            for dimension in (schema_json or {}).get(key, []) or []:
                if isinstance(dimension, dict) and isinstance(dimension.get("id"), str):
                    weights[(score.jd_id, dimension["id"])] = Decimal(
                        str(dimension.get("weight", 0))
                    )
        if score.grade == "rejected":
            rejected.append(
                RejectedScore(
                    score_id=score.id,
                    jd_id=score.jd_id,
                    hard_filter_result=score.hard_filter_result or {},
                    rule_dimensions=score.rule_dimensions or {},
                    judge_dimensions=score.judge_dimensions,
                )
            )

    return BatchRejectionReport.model_validate(
        {
            "filters": {"batch_id": batch_id, "jd_code": jd_code},
            "window_start": resolved_start,
            "window_end": resolved_end,
            "total_scored": len(rows),
            "total_rejected": len(rejected),
            "grade_counts": grade_counts,
            "reasons": aggregate_rejection_reasons(rejected, weights),
        }
    )
