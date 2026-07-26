from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import get_settings
from backend.app.database import get_db
from backend.app.deps import require_roles
from backend.app.models import JD, Score, ScoreCrossCheck, User
from backend.app.schemas.cross_check import (
    BackfillResult,
    SuspiciousPage,
)
from backend.app.services.cross_check.state import ensure_cross_check
from backend.app.services.read.pagination import Page, page_params

router = APIRouter(prefix="/api/v1", tags=["cross-check"])
READ_ROLES = ("hr", "hr_lead", "admin")
ADMIN_ROLES = ("admin",)

MAX_BACKFILL_WINDOW = timedelta(days=90)

_INVALID_WINDOW = {
    "code": "invalid_cross_check_window",
    "message": "Cross-check window must be timezone-aware and increasing",
}
_WINDOW_TOO_LARGE = {
    "code": "cross_check_window_too_large",
    "message": "Cross-check window cannot exceed 90 days",
}
_INVALID_LIMIT = {
    "code": "invalid_cross_check_limit",
    "message": "Cross-check limit is out of range",
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


def _current_rows() -> Select:
    """Only the newest configuration per score may represent it."""
    greatest = (
        select(func.max(ScoreCrossCheck.id))
        .where(ScoreCrossCheck.score_id == Score.id)
        .correlate(Score)
        .scalar_subquery()
    )
    return (
        select(ScoreCrossCheck, Score, JD.code)
        .join(Score, Score.id == ScoreCrossCheck.score_id)
        .join(JD, JD.id == Score.jd_id)
        .where(
            ScoreCrossCheck.id == greatest,
            ScoreCrossCheck.state == "completed",
            ScoreCrossCheck.absolute_diff >= ScoreCrossCheck.threshold_snapshot,
        )
    )


@router.get("/cross-checks/suspicious", response_model=SuspiciousPage)
async def suspicious(
    jd_code: str | None = Query(None),
    min_diff: float | None = Query(None, ge=0),
    reason: str | None = Query(None),
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    page: Page = Depends(page_params),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_roles(*READ_ROLES)),
) -> SuspiciousPage:
    start = _parse(from_)
    end = _parse(to)
    if start is not None and end is not None and start >= end:
        raise HTTPException(status_code=422, detail=_INVALID_WINDOW)

    statement = _current_rows()
    if jd_code is not None:
        statement = statement.where(JD.code == jd_code)
    if min_diff is not None:
        statement = statement.where(
            ScoreCrossCheck.absolute_diff >= Decimal(str(min_diff))
        )
    if reason is not None:
        statement = statement.where(
            ScoreCrossCheck.sample_reasons.contains([reason])
        )
    if start is not None:
        statement = statement.where(ScoreCrossCheck.completed_at >= start)
    if end is not None:
        statement = statement.where(ScoreCrossCheck.completed_at < end)

    total = (
        await db.execute(select(func.count()).select_from(statement.subquery()))
    ).scalar_one()
    rows = (
        await db.execute(
            statement.order_by(
                ScoreCrossCheck.completed_at.desc(), ScoreCrossCheck.id.desc()
            )
            .offset(page.offset)
            .limit(page.page_size)
        )
    ).all()

    return SuspiciousPage.model_validate(
        {
            "items": [
                {
                    "cross_check_id": row.id,
                    "score_id": score.id,
                    "candidate_id": score.candidate_id,
                    "jd_code": jd_code_value,
                    "primary_total_score": Decimal(str(score.total_score)),
                    "secondary_total_score": row.secondary_total_score,
                    "absolute_diff": row.absolute_diff,
                    "threshold": row.threshold_snapshot,
                    "secondary_dimensions": row.secondary_dimensions or [],
                    "sample_reasons": row.sample_reasons or [],
                    "secondary_model": row.secondary_model,
                    "completed_at": row.completed_at,
                }
                for row, score, jd_code_value in rows
            ],
            "page": page.page,
            "page_size": page.page_size,
            "total": int(total),
        }
    )


@router.post("/cross-checks/backfill", response_model=BackfillResult)
async def backfill(
    limit: int = Query(...),
    dry_run: bool = Query(True),
    jd_code: str | None = Query(None),
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_roles(*ADMIN_ROLES)),
) -> BackfillResult:
    settings = get_settings()
    if not 1 <= limit <= settings.CROSS_ENGINE_BACKFILL_MAX:
        raise HTTPException(status_code=422, detail=_INVALID_LIMIT)

    now = datetime.now(timezone.utc)
    end = _parse(to) or now
    start = _parse(from_) or (end - timedelta(days=30))
    if start >= end:
        raise HTTPException(status_code=422, detail=_INVALID_WINDOW)
    if end - start > MAX_BACKFILL_WINDOW:
        raise HTTPException(status_code=422, detail=_WINDOW_TOO_LARGE)

    model = settings.CROSS_ENGINE_MODEL
    prompt_version = "resume_judge_v1"

    candidates = select(Score).where(
        Score.judge_dimensions.isnot(None),
        Score.created_at >= start,
        Score.created_at < end,
    )
    if jd_code is not None:
        candidates = candidates.join(JD, JD.id == Score.jd_id).where(JD.code == jd_code)
    scores = list(
        (
            await db.execute(
                candidates.order_by(Score.created_at.desc(), Score.id.desc()).limit(limit)
            )
        ).scalars()
    )

    existing_ids = set(
        (
            await db.execute(
                select(ScoreCrossCheck.score_id).where(
                    ScoreCrossCheck.score_id.in_([s.id for s in scores] or [0]),
                    ScoreCrossCheck.secondary_model == model,
                    ScoreCrossCheck.prompt_version == prompt_version,
                )
            )
        ).scalars()
    )
    pending = [score for score in scores if score.id not in existing_ids]

    queued: list[int] = []
    if not dry_run and model:
        for score in pending:
            row = await ensure_cross_check(
                db,
                score_id=score.id,
                secondary_model=model,
                prompt_version=prompt_version,
                reasons=["admin_backfill"],
                threshold=Decimal(str(settings.CROSS_ENGINE_DIFF_THRESHOLD)),
            )
            queued.append(row.id)
        # Commit before delivery so a worker never chases an uncommitted row.
        await db.commit()
        from backend.app.tasks.ingest import _send_cross_checks

        _send_cross_checks(queued)

    return BackfillResult(
        dry_run=dry_run,
        selected=len(scores),
        already_existing=len(existing_ids),
        would_queue=len(pending),
        newly_queued=len(queued),
    )
