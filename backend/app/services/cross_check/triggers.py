from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import get_settings
from backend.app.models import Feedback, GoldenSet, RuleVersion, Score
from backend.app.rules.schema import RuleSchema
from backend.app.services.cross_check.sampling import (
    CrossCheckContext,
    eligible,
    trigger_reasons,
)
from backend.app.services.cross_check.state import ensure_cross_check

JUDGE_PROMPT_VERSION_FALLBACK = "resume_judge_v1"


def _persisted_dimensions(score: Score) -> list[dict[str, Any]]:
    entries = (score.judge_dimensions or {}).get("dimensions")
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, dict)]


async def queue_cross_check_for_score(
    db: AsyncSession,
    *,
    score: Score,
    admin_backfill: bool = False,
    prompt_version: str | None = None,
) -> list[int]:
    """Queue a second opinion for one score if any trigger fires.

    The caller owns the commit, so the queue row lands in the same transaction
    as whatever caused it — a rolled-back feedback or score can never leave an
    orphaned check behind. Returns the queued row ids for post-commit delivery.
    """
    settings = get_settings()
    version = await db.get(RuleVersion, score.rule_version_id)
    if version is None:
        return []
    try:
        schema = RuleSchema.model_validate(version.schema_json)
    except Exception:
        # An invalid bound schema cannot be re-judged meaningfully.
        return []

    golden_label = (
        await db.execute(
            select(GoldenSet.label).where(
                GoldenSet.candidate_id == score.candidate_id,
                GoldenSet.jd_id == score.jd_id,
            )
        )
    ).scalar_one_or_none()
    # Any reviewer disagreement on this score is enough to warrant a second look.
    disagreed = (
        await db.execute(
            select(Feedback.id).where(
                Feedback.score_id == score.id, Feedback.ai_agreed.is_(False)
            )
        )
    ).first()

    context = CrossCheckContext(
        score_id=score.id,
        jd_id=score.jd_id,
        prompt_version=prompt_version or JUDGE_PROMPT_VERSION_FALLBACK,
        secondary_model=settings.CROSS_ENGINE_MODEL,
        primary_model=settings.LLM_MODEL_JUDGE,
        schema_dimension_ids=[dim.id for dim in schema.judge_dimensions],
        judge_dimensions=_persisted_dimensions(score),
        grade=score.grade,
        sample_percent=settings.CROSS_ENGINE_SAMPLE_PERCENT,
        low_confidence=Decimal(str(settings.CROSS_ENGINE_LOW_CONFIDENCE)),
        golden_label=golden_label,
        ai_agreed=False if disagreed is not None else None,
        weights={
            (score.jd_id, dim.id): Decimal(str(dim.weight))
            for dim in schema.judge_dimensions
        },
    )
    if not eligible(context):
        return []
    reasons = trigger_reasons(context, admin_backfill=admin_backfill)
    if not reasons:
        return []

    row = await ensure_cross_check(
        db,
        score_id=score.id,
        secondary_model=settings.CROSS_ENGINE_MODEL,
        prompt_version=context.prompt_version,
        reasons=reasons,
        threshold=Decimal(str(settings.CROSS_ENGINE_DIFF_THRESHOLD)),
    )
    return [row.id]
