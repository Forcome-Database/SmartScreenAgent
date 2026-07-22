from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import JD, Candidate, GoldenSet, RuleVersion, Score
from backend.app.rules.schema import RuleSchema
from backend.app.scoring.hard_filter import run_hard_filters
from backend.app.scoring.pipeline import _grade_from
from backend.app.scoring.rule_engine import score_dimensions
from backend.app.services.golden_set import golden_metrics, metric_stats


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


def _metrics_dict(
    tp: int,
    fp: int,
    tn: int,
    fn: int,
    *,
    indeterminate: int,
    borderline_excluded: int,
    uncovered: int,
) -> dict[str, Any]:
    return {
        **metric_stats(tp, fp, tn, fn),
        "evaluated": tp + fp + tn + fn,
        "indeterminate": indeterminate,
        "borderline_excluded": borderline_excluded,
        "uncovered": uncovered,
    }


async def evaluate_draft(
    db: AsyncSession,
    *,
    jd: JD,
    draft: RuleVersion,
) -> tuple[dict[str, Any], bool]:
    if draft.status != "draft":
        raise NotADraft
    schema = RuleSchema.model_validate(draft.schema_json)

    label_rows = (
        await db.execute(
            select(GoldenSet.label, func.count())
            .where(GoldenSet.jd_id == jd.id)
            .group_by(GoldenSet.label)
        )
    ).all()
    label_counts: dict[str, int] = {label: count for label, count in label_rows}
    advance_reject_total = label_counts.get("advance", 0) + label_counts.get("reject", 0)
    borderline_excluded = label_counts.get("borderline", 0)

    latest = (
        select(
            Score.candidate_id,
            Score.total_score,
            Score.rule_dimensions,
            Score.judge_dimensions,
            func.row_number()
            .over(
                partition_by=(Score.candidate_id, Score.jd_id),
                order_by=(Score.created_at.desc(), Score.id.desc()),
            )
            .label("rn"),
        )
        .where(Score.jd_id == jd.id)
        .subquery()
    )
    rows = (
        await db.execute(
            select(
                GoldenSet.label,
                Candidate.extracted_json,
                latest.c.total_score,
                latest.c.rule_dimensions,
                latest.c.judge_dimensions,
            )
            .select_from(GoldenSet)
            .join(Candidate, Candidate.id == GoldenSet.candidate_id)
            .join(
                latest,
                and_(
                    latest.c.candidate_id == GoldenSet.candidate_id,
                    latest.c.rn == 1,
                ),
            )
            .where(
                GoldenSet.jd_id == jd.id,
                GoldenSet.label.in_(("advance", "reject")),
            )
        )
    ).all()

    counts = {"tp": 0, "fp": 0, "tn": 0, "fn": 0}
    indeterminate = 0
    for label, extracted, total_score, rule_dimensions, judge_dimensions in rows:
        grade = whatif_grade(
            schema,
            extracted or {},
            stored_rule_subtotal=float((rule_dimensions or {}).get("subtotal", 0) or 0),
            stored_total=float(total_score),
            stored_hard_rejected=judge_dimensions is None,
        )
        if grade is None:
            indeterminate += 1
            continue
        counts[bucket(label, grade)] += 1

    metrics = _metrics_dict(
        counts["tp"],
        counts["fp"],
        counts["tn"],
        counts["fn"],
        indeterminate=indeterminate,
        borderline_excluded=borderline_excluded,
        uncovered=advance_reject_total - len(rows),
    )
    draft.golden_set_metrics = metrics
    await db.commit()

    judge_dimensions_changed = False
    if jd.active_rule_version_id is not None:
        active = (
            await db.execute(
                select(RuleVersion).where(RuleVersion.id == jd.active_rule_version_id)
            )
        ).scalar_one_or_none()
        if active is not None:
            judge_dimensions_changed = draft.schema_json.get(
                "judge_dimensions"
            ) != active.schema_json.get("judge_dimensions")
    return metrics, judge_dimensions_changed


async def active_baseline(db: AsyncSession, *, jd: JD) -> dict[str, Any] | None:
    if jd.active_rule_version_id is None:
        return None
    overall = (await golden_metrics(db, jd.code)).overall
    return {
        "confusion": overall.confusion.model_dump(),
        "precision": overall.precision,
        "recall": overall.recall,
        "f1": overall.f1,
        "accuracy": overall.accuracy,
        "evaluated": (
            overall.confusion.tp
            + overall.confusion.fp
            + overall.confusion.tn
            + overall.confusion.fn
        ),
        "indeterminate": 0,
        "borderline_excluded": overall.borderline_excluded,
        "uncovered": overall.uncovered,
    }
