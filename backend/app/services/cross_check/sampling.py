from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from typing import Any

UNKNOWN_TIER = "unknown"
REJECTED_GRADE = "rejected"

# Reasons are always reported in this order so a stored `sample_reasons` list is
# comparable between rows and across runs.
TRIGGER_ORDER: tuple[str, ...] = (
    "deterministic_sample",
    "low_confidence",
    "golden_error",
    "ai_hr_disagreement",
    "admin_backfill",
)


@dataclass(frozen=True)
class CrossCheckContext:
    """Everything a sampling decision needs, with no prompt or resume content."""

    score_id: int
    jd_id: int
    prompt_version: str
    secondary_model: str
    primary_model: str
    schema_dimension_ids: list[str]
    judge_dimensions: list[dict[str, Any]]
    grade: str
    sample_percent: int
    low_confidence: Decimal
    golden_label: str | None = None
    ai_agreed: bool | None = None
    weights: dict[tuple[int, str], Decimal] = field(default_factory=dict)


def sample_bucket(score_id: int, prompt_version: str) -> int:
    """A stable 0..99 bucket for one score under one prompt version.

    Hashing rather than using the ID directly keeps the sample spread out, and
    including the prompt version means re-prompting re-samples instead of
    permanently exempting the same scores.
    """
    digest = sha256(f"wp7:{score_id}:{prompt_version}".encode()).digest()[:8]
    return int.from_bytes(digest, "big") % 100


def eligible(context: CrossCheckContext) -> bool:
    """A second opinion is only meaningful against a different, bound engine."""
    if not context.secondary_model or context.secondary_model == context.primary_model:
        return False
    if not context.schema_dimension_ids or not context.judge_dimensions:
        return False
    known = set(context.schema_dimension_ids)
    return any(
        isinstance(entry, dict) and entry.get("id") in known
        for entry in context.judge_dimensions
    )


def _confidence(value: object) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not parsed.is_finite() or not Decimal("0") <= parsed <= Decimal("1"):
        return None
    return parsed


def weighted_confidence(context: CrossCheckContext) -> Decimal | None:
    """Weighted mean judge confidence, or None when nothing usable remains."""
    total_weight = Decimal("0")
    weighted_sum = Decimal("0")
    for entry in context.judge_dimensions:
        if not isinstance(entry, dict):
            continue
        dimension_id = entry.get("id")
        if not isinstance(dimension_id, str):
            continue
        # Strictly the bound weights: an unbound dimension has no agreed say in
        # the mean, and silently defaulting it to 1 would misreport confidence.
        weight = context.weights.get((context.jd_id, dimension_id))
        if weight is None or weight <= 0:
            continue
        if str(entry.get("tier") or UNKNOWN_TIER) == UNKNOWN_TIER:
            continue
        confidence = _confidence(entry.get("confidence"))
        if confidence is None:
            continue
        total_weight += weight
        weighted_sum += confidence * weight
    if total_weight == 0:
        return None
    return weighted_sum / total_weight


def trigger_reasons(
    context: CrossCheckContext, *, admin_backfill: bool = False
) -> list[str]:
    """Why this score deserves a second opinion, deduplicated and ordered."""
    reasons: set[str] = set()

    if admin_backfill:
        reasons.add("admin_backfill")

    if context.sample_percent > 0 and sample_bucket(
        context.score_id, context.prompt_version
    ) < context.sample_percent:
        reasons.add("deterministic_sample")

    confidence = weighted_confidence(context)
    # No usable confidence at all is itself a low-confidence signal.
    if confidence is None or confidence < context.low_confidence:
        reasons.add("low_confidence")

    if context.golden_label in {"advance", "reject"}:
        predicted_advance = context.grade != REJECTED_GRADE
        if predicted_advance != (context.golden_label == "advance"):
            reasons.add("golden_error")

    if context.ai_agreed is False:
        reasons.add("ai_hr_disagreement")

    return [reason for reason in TRIGGER_ORDER if reason in reasons]
