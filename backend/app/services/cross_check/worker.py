from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from backend.app.config import get_settings
from backend.app.database import AsyncSessionLocal
from backend.app.models import Candidate, RuleVersion, Score
from backend.app.rules.schema import RuleSchema
from backend.app.scoring.llm_judge import LLMJudge
from backend.app.services.cross_check.state import (
    claim_cross_check,
    complete_cross_check,
    fail_cross_check,
)
from backend.app.services.llm.errors import (
    LLMConfigurationError,
    LLMInvalidOutputError,
    LLMInvalidResponseError,
    LLMUnavailableError,
    ModelPriceMissing,
    UsageLedgerUnavailable,
)
from backend.app.services.llm.usage import LLMCallContext

logger = logging.getLogger(__name__)


class SourceMissing(Exception):
    """The score, rule version, candidate, or resume text is no longer usable."""


@dataclass(frozen=True)
class _Source:
    score_id: int
    jd_id: int
    rule_version_id: int
    primary_total: Decimal
    rule_subtotal: Decimal
    resume_markdown: str
    schema: RuleSchema


def _sanitize(dimensions: list[Any]) -> list[dict[str, Any]]:
    """Keep only the four comparable fields.

    Evidence, reasoning, and suggested questions from the secondary engine are
    deliberately dropped: a cross-check exists to compare numbers, and storing a
    second copy of candidate-derived text doubles the leak surface for no gain.
    """
    return [
        {
            "id": item.id,
            "tier": item.tier,
            "score": item.score,
            "confidence": item.confidence,
        }
        for item in dimensions
    ]


async def _load_source(row_score_id: int) -> _Source:
    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                select(Score, RuleVersion, Candidate)
                .join(RuleVersion, RuleVersion.id == Score.rule_version_id)
                .join(Candidate, Candidate.id == Score.candidate_id)
                .where(Score.id == row_score_id)
            )
        ).first()
        if row is None:
            raise SourceMissing("score, rule version, or candidate is gone")
        score, version, candidate = row
        markdown = candidate.parsed_markdown or ""
        if not markdown.strip():
            raise SourceMissing("candidate has no parsed resume text")
        try:
            schema = RuleSchema.model_validate(version.schema_json)
        except Exception as exc:
            raise SourceMissing("bound rule schema is no longer valid") from exc
        try:
            subtotal = Decimal(str((score.rule_dimensions or {})["subtotal"]))
        except (KeyError, TypeError, InvalidOperation) as exc:
            raise SourceMissing("score has no stored rule subtotal") from exc
        return _Source(
            score_id=score.id,
            jd_id=score.jd_id,
            rule_version_id=score.rule_version_id,
            primary_total=Decimal(str(score.total_score)),
            rule_subtotal=subtotal,
            resume_markdown=markdown,
            schema=schema,
        )


def _classify(exc: BaseException) -> tuple[str, bool]:
    """Map a failure onto (error_code, retryable). Order is significant."""
    if isinstance(exc, ModelPriceMissing):
        return "model_price_missing", False
    if isinstance(exc, UsageLedgerUnavailable):
        return "usage_ledger_unavailable", True
    if isinstance(exc, LLMConfigurationError):
        return "provider_configuration_error", False
    if isinstance(exc, (LLMInvalidResponseError, LLMInvalidOutputError)):
        return "invalid_secondary_output", False
    if isinstance(exc, LLMUnavailableError):
        return "provider_unavailable", True
    if isinstance(exc, SourceMissing):
        return "source_missing", False
    if isinstance(exc, (SQLAlchemyError, OSError)):
        return "database_unavailable", True
    return "cross_check_unexpected", True


async def run_cross_check(row_id: int, *, judge: LLMJudge | None = None) -> dict[str, Any]:
    """Claim, re-judge with the secondary engine, and record the outcome.

    Each database phase is its own short session so the provider wait never
    holds a transaction, matching the rule the rest of WP7 follows.
    """
    settings = get_settings()
    now = datetime.now(UTC)

    async with AsyncSessionLocal() as session:
        claimed = await claim_cross_check(
            session,
            row_id=row_id,
            now=now,
            lease_seconds=settings.CROSS_ENGINE_LEASE_SECONDS,
            max_attempts=settings.CROSS_ENGINE_MAX_ATTEMPTS,
        )
        await session.commit()
    if claimed is None:
        # Another worker already owns it, or it is out of attempts.
        return {"row_id": row_id, "claimed": False}

    try:
        source = await _load_source(claimed.score_id)
        result = await (judge or LLMJudge()).score(
            resume_text=source.resume_markdown,
            dims=source.schema.judge_dimensions,
            context=LLMCallContext(
                operation="cross_check",
                call_group_id=uuid4(),
                score_id=source.score_id,
                jd_id=source.jd_id,
                rule_version_id=source.rule_version_id,
            ),
            model_override=claimed.secondary_model,
        )
        judge_subtotal = sum(
            (Decimal(str(item.score or 0)) for item in result.dimensions), Decimal("0")
        )
        secondary_total = source.rule_subtotal + judge_subtotal
    except BaseException as exc:
        error_code, _retryable = _classify(exc)
        async with AsyncSessionLocal() as session:
            await fail_cross_check(
                session,
                row_id=claimed.id,
                lease_token=claimed.lease_token,
                error_code=error_code,
                max_attempts=settings.CROSS_ENGINE_MAX_ATTEMPTS,
            )
            await session.commit()
        logger.warning(
            "cross check failed",
            extra={"row_id": claimed.id, "error_code": error_code},
        )
        return {"row_id": row_id, "claimed": True, "error_code": error_code}

    async with AsyncSessionLocal() as session:
        completed = await complete_cross_check(
            session,
            row_id=claimed.id,
            lease_token=claimed.lease_token,
            secondary_total=secondary_total,
            secondary_dimensions=_sanitize(result.dimensions),
            now=datetime.now(UTC),
        )
        await session.commit()
    return {"row_id": row_id, "claimed": True, "completed": completed}
