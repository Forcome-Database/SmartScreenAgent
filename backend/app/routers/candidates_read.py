from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import get_settings
from backend.app.database import get_db
from backend.app.deps import require_roles
from backend.app.models import AuditLog, Candidate, User
from backend.app.schemas.read import (
    CandidateDetail,
    CandidateList,
    RankedCandidateList,
    RawFileLink,
    ScoreDetail,
)
from backend.app.services.read.candidates import (
    get_candidate_detail,
    get_score_detail,
    list_candidates,
    list_ranked_for_jd,
)
from backend.app.services.read.pagination import Page, page_params
from backend.app.services.storage import ResumeStorageService, StorageError

router = APIRouter(prefix="/api/v1", tags=["read"])
READ_ROLES = ("hr", "hr_lead", "admin")


def _not_found(resource: str) -> HTTPException:
    return HTTPException(
        status_code=404, detail={"code": "not_found", "message": f"{resource} not found"}
    )


@router.get("/jds/{code}/candidates", response_model=RankedCandidateList)
async def ranked_candidates(
    code: str,
    grade: str | None = None,
    page: Page = Depends(page_params),
    db: AsyncSession = Depends(get_db),
    _u: User = Depends(require_roles(*READ_ROLES)),
) -> RankedCandidateList:
    result = await list_ranked_for_jd(db, code, grade, page)
    if result is None:
        raise _not_found("JD")
    items, total = result
    return RankedCandidateList(items=items, page=page.page, page_size=page.page_size, total=total)


@router.get("/candidates", response_model=CandidateList)
async def candidates(
    state: str | None = None,
    page: Page = Depends(page_params),
    db: AsyncSession = Depends(get_db),
    _u: User = Depends(require_roles(*READ_ROLES)),
) -> CandidateList:
    items, total = await list_candidates(db, state, page)
    return CandidateList(items=items, page=page.page, page_size=page.page_size, total=total)


@router.get("/candidates/{candidate_id}", response_model=CandidateDetail)
async def candidate_detail(
    candidate_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*READ_ROLES)),
) -> CandidateDetail:
    trace_id = structlog.contextvars.get_contextvars().get("trace_id")
    detail = await get_candidate_detail(
        db, candidate_id, actor=f"user:{user.id}", trace_id=trace_id
    )
    if detail is None:
        raise _not_found("candidate")
    return detail


@router.get("/candidates/{candidate_id}/scores/{score_id}", response_model=ScoreDetail)
async def score_detail(
    candidate_id: int,
    score_id: int,
    db: AsyncSession = Depends(get_db),
    _u: User = Depends(require_roles(*READ_ROLES)),
) -> ScoreDetail:
    detail = await get_score_detail(db, candidate_id, score_id)
    if detail is None:
        raise _not_found("score")
    return detail


@router.get("/candidates/{candidate_id}/raw-file", response_model=RawFileLink)
async def raw_file(
    candidate_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_roles(*READ_ROLES)),
) -> RawFileLink:
    candidate = (
        await db.execute(select(Candidate).where(Candidate.id == candidate_id))
    ).scalar_one_or_none()
    if candidate is None or not candidate.raw_file_key:
        raise _not_found("candidate raw file")
    settings = get_settings()
    ttl = settings.RAW_FILE_PRESIGN_TTL_SECONDS
    try:
        url = await ResumeStorageService().presigned_get_url(
            candidate.raw_file_key, expires_seconds=ttl
        )
    except StorageError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "object_storage_unavailable",
                "message": "Resume storage is unavailable",
            },
        ) from exc
    trace_id = structlog.contextvars.get_contextvars().get("trace_id")
    db.add(
        AuditLog(
            event_type="raw_file_access",
            actor=f"user:{user.id}",
            target_type="candidate",
            target_id=candidate_id,
            payload={"purpose": "raw_file_download", "trace_id": trace_id},
        )
    )
    await db.commit()
    return RawFileLink(url=url, expires_in_seconds=ttl)
