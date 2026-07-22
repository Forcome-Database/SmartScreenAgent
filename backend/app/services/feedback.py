# backend/app/services/feedback.py
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import Feedback, Score, User


class FeedbackReasonRequired(Exception):
    """Raised when a disagreement (ai_agreed is False) is submitted without a reason."""


def derive_ai_agreed(grade: str, decision: str) -> bool | None:
    """AI reject == grade "rejected"; hold yields None (excluded from agreement)."""
    if decision == "hold":
        return None
    ai_reject = grade == "rejected"
    hr_reject = decision == "reject"
    return ai_reject == hr_reject


async def upsert_feedback(
    db: AsyncSession, *, score: Score, reviewer_id: int, decision: str, reason: str | None
) -> Feedback:
    ai_agreed = derive_ai_agreed(score.grade, decision)
    normalized_reason = (reason or "").strip() or None
    if ai_agreed is False and not normalized_reason:
        raise FeedbackReasonRequired()
    stmt = (
        pg_insert(Feedback)
        .values(
            score_id=score.id,
            reviewer_user_id=reviewer_id,
            decision=decision,
            reason=normalized_reason,
            ai_agreed=ai_agreed,
        )
        .on_conflict_do_update(
            constraint="uq_feedback_score_reviewer",
            set_={
                "decision": decision,
                "reason": normalized_reason,
                "ai_agreed": ai_agreed,
                "updated_at": func.now(),
            },
        )
        .returning(Feedback.id)
    )
    feedback_id = (await db.execute(stmt)).scalar_one()
    await db.commit()
    return (
        await db.execute(select(Feedback).where(Feedback.id == feedback_id))
    ).scalar_one()


async def list_feedback(db: AsyncSession, score_id: int) -> list[tuple[Feedback, str]]:
    rows = (
        await db.execute(
            select(Feedback, User.display_name)
            .join(User, User.id == Feedback.reviewer_user_id)
            .where(Feedback.score_id == score_id)
            .order_by(Feedback.updated_at.desc().nullslast(), Feedback.id.desc())
        )
    ).all()
    return [(fb, name) for fb, name in rows]
