from __future__ import annotations

from typing import Any

from backend.app.rules.schema import RuleSchema
from backend.app.scoring.hard_filter import run_hard_filters
from backend.app.scoring.pipeline import _grade_from
from backend.app.scoring.rule_engine import score_dimensions


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
