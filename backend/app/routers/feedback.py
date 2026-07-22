# backend/app/routers/feedback.py
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.database import get_db
from backend.app.deps import require_roles
from backend.app.models import Feedback, Score, User
from backend.app.schemas.feedback import FeedbackItem, FeedbackUpsertRequest
from backend.app.services.feedback import (
    FeedbackReasonRequired,
    list_feedback,
    upsert_feedback,
)

router = APIRouter(prefix="/api/v1", tags=["feedback"])
ROLES = ("hr", "hr_lead", "admin")


async def _load_score(db: AsyncSession, candidate_id: int, score_id: int) -> Score:
    score = (
        await db.execute(
            select(Score).where(Score.id == score_id, Score.candidate_id == candidate_id)
        )
    ).scalar_one_or_none()
    if score is None:
        raise HTTPException(
            status_code=404, detail={"code": "not_found", "message": "score not found"}
        )
    return score


def _serialize(fb: Feedback, display_name: str) -> FeedbackItem:
    return FeedbackItem(
        id=fb.id, score_id=fb.score_id, reviewer_user_id=fb.reviewer_user_id,
        reviewer_display_name=display_name, decision=fb.decision, reason=fb.reason,
        ai_agreed=fb.ai_agreed, created_at=fb.created_at, updated_at=fb.updated_at,
    )


@router.put("/candidates/{candidate_id}/scores/{score_id}/feedback", response_model=FeedbackItem)
async def upsert(
    candidate_id: int, score_id: int, payload: FeedbackUpsertRequest,
    db: AsyncSession = Depends(get_db), user: User = Depends(require_roles(*ROLES)),
) -> FeedbackItem:
    score = await _load_score(db, candidate_id, score_id)
    try:
        fb = await upsert_feedback(
            db, score=score, reviewer_id=user.id, decision=payload.decision, reason=payload.reason
        )
    except FeedbackReasonRequired as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "feedback_reason_required", "message": "与 AI 不一致时必须填写理由"},
        ) from exc
    return _serialize(fb, user.display_name)


@router.get(
    "/candidates/{candidate_id}/scores/{score_id}/feedback", response_model=list[FeedbackItem]
)
async def list_for_score(
    candidate_id: int, score_id: int,
    db: AsyncSession = Depends(get_db), _u: User = Depends(require_roles(*ROLES)),
) -> list[FeedbackItem]:
    await _load_score(db, candidate_id, score_id)
    return [_serialize(fb, name) for fb, name in await list_feedback(db, score_id)]
