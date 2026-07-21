from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import JD, AuditLog, Candidate, IngestionJob, RuleVersion, Score
from backend.app.schemas.read import (
    CandidateDetail,
    CandidateListItem,
    CandidateScoreSummary,
    RankedCandidateItem,
    ScoreDetail,
)
from backend.app.security.crypto import decrypt_pii
from backend.app.services.read.pagination import Page


async def list_ranked_for_jd(
    db: AsyncSession, jd_code: str, grade: str | None, page: Page
) -> tuple[list[RankedCandidateItem], int] | None:
    jd = (await db.execute(select(JD).where(JD.code == jd_code))).scalar_one_or_none()
    if jd is None:
        return None
    if not jd.active_rule_version_id:
        return [], 0
    base = (
        select(Score, RuleVersion.version)
        .join(RuleVersion, RuleVersion.id == Score.rule_version_id)
        .where(Score.jd_id == jd.id, Score.rule_version_id == jd.active_rule_version_id)
    )
    if grade is not None:
        base = base.where(Score.grade == grade)
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (
        await db.execute(
            base.order_by(Score.total_score.desc(), Score.id.asc())
            .offset(page.offset)
            .limit(page.page_size)
        )
    ).all()
    items = [
        RankedCandidateItem(
            candidate_id=score.candidate_id,
            score_id=score.id,
            total_score=float(score.total_score),
            grade=score.grade,
            rule_version=version,
            scored_at=score.created_at,
        )
        for score, version in rows
    ]
    return items, total


async def list_candidates(
    db: AsyncSession, state: str | None, page: Page
) -> tuple[list[CandidateListItem], int]:
    # latest ingestion job state per candidate via a correlated subquery
    latest_state = (
        select(IngestionJob.state)
        .where(IngestionJob.candidate_id == Candidate.id)
        .order_by(IngestionJob.created_at.desc())
        .limit(1)
        .scalar_subquery()
    )
    base = select(Candidate.id, Candidate.created_at, latest_state.label("latest_state"))
    if state is not None:
        base = base.where(latest_state == state)
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (
        await db.execute(
            base.order_by(Candidate.created_at.desc()).offset(page.offset).limit(page.page_size)
        )
    ).all()
    items: list[CandidateListItem] = []
    for candidate_id, created_at, state_value in rows:
        codes = (
            await db.execute(
                select(JD.code)
                .join(Score, Score.jd_id == JD.id)
                .where(Score.candidate_id == candidate_id)
                .distinct()
            )
        ).scalars().all()
        items.append(
            CandidateListItem(
                candidate_id=candidate_id,
                created_at=created_at,
                latest_state=state_value,
                scored_jd_codes=list(codes),
            )
        )
    return items, total


async def get_candidate_detail(
    db: AsyncSession, candidate_id: int, *, actor: str, trace_id: str | None
) -> CandidateDetail | None:
    candidate = (
        await db.execute(select(Candidate).where(Candidate.id == candidate_id))
    ).scalar_one_or_none()
    if candidate is None:
        return None
    extracted = candidate.extracted_json or {}
    score_rows = (
        await db.execute(
            select(Score, JD.code, RuleVersion.version)
            .join(JD, JD.id == Score.jd_id)
            .join(RuleVersion, RuleVersion.id == Score.rule_version_id)
            .where(Score.candidate_id == candidate_id)
        )
    ).all()
    db.add(
        AuditLog(
            event_type="pii_decrypt",
            actor=actor,
            target_type="candidate",
            target_id=candidate_id,
            payload={"purpose": "candidate_detail", "trace_id": trace_id},
        )
    )
    await db.commit()
    return CandidateDetail(
        candidate_id=candidate.id,
        name=decrypt_pii(candidate.name_cipher),
        phone=decrypt_pii(candidate.phone_cipher) if candidate.phone_cipher else None,
        email=decrypt_pii(candidate.email_cipher) if candidate.email_cipher else None,
        age=extracted.get("age"),
        education=extracted.get("education"),
        experiences=extracted.get("experiences", []),
        source=candidate.source,
        created_at=candidate.created_at,
        scores=[
            CandidateScoreSummary(
                score_id=s.id,
                jd_code=code,
                total_score=float(s.total_score),
                grade=s.grade,
                rule_version=version,
            )
            for s, code, version in score_rows
        ],
    )


async def get_score_detail(
    db: AsyncSession, candidate_id: int, score_id: int
) -> ScoreDetail | None:
    row = (
        await db.execute(
            select(Score, JD.code, RuleVersion.version)
            .join(JD, JD.id == Score.jd_id)
            .join(RuleVersion, RuleVersion.id == Score.rule_version_id)
            .where(Score.id == score_id, Score.candidate_id == candidate_id)
        )
    ).first()
    if row is None:
        return None
    score, jd_code, version = row
    return ScoreDetail(
        score_id=score.id,
        candidate_id=score.candidate_id,
        jd_code=jd_code,
        rule_version=version,
        total_score=float(score.total_score),
        grade=score.grade,
        hard_filter_result=score.hard_filter_result,
        rule_dimensions=score.rule_dimensions,
        judge_dimensions=score.judge_dimensions,
    )
