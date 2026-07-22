# backend/app/services/feedback.py
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import JD, Feedback, Score, User
from backend.app.schemas.feedback import (
    AgreementStats,
    DisagreementItem,
    DisagreementPage,
    FeedbackReport,
    JDAgreement,
)
from backend.app.services.read.pagination import Page


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


def agreement_stats(agreed: int, disagreed: int, hold: int) -> dict:
    decided = agreed + disagreed
    rate = (agreed / decided) if decided else None
    return {
        "total": agreed + disagreed + hold,
        "agreed": agreed,
        "disagreed": disagreed,
        "hold": hold,
        "agreement_rate": rate,
    }


async def feedback_report(db: AsyncSession, jd_code: str | None, page: Page) -> FeedbackReport:
    # counts grouped by (jd_code, ai_agreed) — ai_agreed True/False/None
    grouped = (
        select(JD.code, Feedback.ai_agreed, func.count().label("n"))
        .select_from(Feedback)
        .join(Score, Score.id == Feedback.score_id)
        .join(JD, JD.id == Score.jd_id)
    )
    if jd_code is not None:
        grouped = grouped.where(JD.code == jd_code)
    grouped = grouped.group_by(JD.code, Feedback.ai_agreed)
    rows = (await db.execute(grouped)).all()

    per_jd: dict[str, list[int]] = {}
    tot_agreed = tot_disagreed = tot_hold = 0
    for code, ai_agreed, n in rows:
        bucket = per_jd.setdefault(code, [0, 0, 0])  # agreed, disagreed, hold
        if ai_agreed is True:
            bucket[0] += n
            tot_agreed += n
        elif ai_agreed is False:
            bucket[1] += n
            tot_disagreed += n
        else:
            bucket[2] += n
            tot_hold += n

    by_jd = [
        JDAgreement(jd_code=code, **agreement_stats(a, d, h))
        for code, (a, d, h) in sorted(per_jd.items())
    ]
    overall = AgreementStats(**agreement_stats(tot_agreed, tot_disagreed, tot_hold))

    # disagreements list (ai_agreed is False), paginated
    dis_base = (
        select(
            Feedback.id, Feedback.score_id, Score.candidate_id, JD.code,
            Feedback.decision, Feedback.reason, User.display_name, Feedback.updated_at,
        )
        .select_from(Feedback)
        .join(Score, Score.id == Feedback.score_id)
        .join(JD, JD.id == Score.jd_id)
        .join(User, User.id == Feedback.reviewer_user_id)
        .where(Feedback.ai_agreed.is_(False))
    )
    if jd_code is not None:
        dis_base = dis_base.where(JD.code == jd_code)
    total = (await db.execute(select(func.count()).select_from(dis_base.subquery()))).scalar_one()
    dis_rows = (
        await db.execute(
            dis_base.order_by(Feedback.updated_at.desc().nullslast(), Feedback.id.desc())
            .offset(page.offset).limit(page.page_size)
        )
    ).all()
    items = [
        DisagreementItem(
            feedback_id=fid, score_id=sid, candidate_id=cid, jd_code=code,
            decision=dec, reason=reason, reviewer_display_name=name, updated_at=updated,
        )
        for fid, sid, cid, code, dec, reason, name, updated in dis_rows
    ]
    return FeedbackReport(
        overall=overall,
        by_jd=by_jd,
        disagreements=DisagreementPage(
            items=items, page=page.page, page_size=page.page_size, total=total
        ),
    )
