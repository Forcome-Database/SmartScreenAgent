from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import JD, RuleVersion
from backend.app.rules.schema import RuleSchema
from backend.app.scoring.hard_filter import run_hard_filters
from backend.app.scoring.pipeline import _grade_from
from backend.app.scoring.rule_engine import score_dimensions


class InvalidRuleSchema(Exception):
    pass


class VersionExists(Exception):
    pass


class NotADraft(Exception):
    pass


class RegressionNotRecorded(Exception):
    pass


def whatif_grade(
    schema: RuleSchema,
    extracted: dict[str, Any],
    *,
    stored_rule_subtotal: float,
    stored_total: float,
    stored_hard_rejected: bool,
) -> str | None:
    """Re-score with draft rules while reusing the stored judge subtotal."""
    hard_filter = run_hard_filters(candidate=extracted, filters=schema.hard_filters)
    if hard_filter.rejected:
        return "rejected"
    if stored_hard_rejected:
        return None

    rule_results = score_dimensions(extracted, schema.rule_dimensions)
    rule_total = sum((result.get("score") or 0) for result in rule_results)
    judge_total = stored_total - stored_rule_subtotal
    return _grade_from(rule_total + judge_total, schema)


def bucket(label: str, grade: str) -> str:
    """Return the advance-positive confusion-matrix cell."""
    ai_advance = grade != "rejected"
    if label == "advance":
        return "tp" if ai_advance else "fn"
    return "fp" if ai_advance else "tn"


async def create_draft(
    db: AsyncSession,
    *,
    jd: JD,
    schema_json: dict,
    notes: str | None,
) -> RuleVersion:
    try:
        schema = RuleSchema.model_validate(schema_json)
    except ValidationError as exc:
        raise InvalidRuleSchema from exc

    existing_id = (
        await db.execute(
            select(RuleVersion.id).where(
                RuleVersion.jd_id == jd.id,
                RuleVersion.version == schema.version,
            )
        )
    ).scalar_one_or_none()
    if existing_id is not None:
        raise VersionExists

    draft = RuleVersion(
        jd_id=jd.id,
        version=schema.version,
        schema_json=schema_json,
        status="draft",
        published_at=None,
        notes=notes,
    )
    db.add(draft)
    await db.commit()
    await db.refresh(draft)
    return draft


async def publish_draft(
    db: AsyncSession,
    *,
    jd: JD,
    draft: RuleVersion,
    publisher_id: int,
) -> RuleVersion:
    if draft.status != "draft":
        raise NotADraft
    if draft.golden_set_metrics is None:
        raise RegressionNotRecorded

    if jd.active_rule_version_id is not None:
        previous = (
            await db.execute(
                select(RuleVersion).where(RuleVersion.id == jd.active_rule_version_id)
            )
        ).scalar_one_or_none()
        if previous is not None:
            previous.status = "archived"

    draft.status = "published"
    draft.published_at = datetime.now(timezone.utc)
    draft.published_by_user_id = publisher_id
    jd.active_rule_version_id = draft.id
    await db.commit()
    await db.refresh(draft)
    return draft
