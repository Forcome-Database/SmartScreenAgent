from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import Score, ScoreCrossCheck
from backend.app.services.cross_check.sampling import TRIGGER_ORDER

QUEUED = "queued"
RUNNING = "running"
COMPLETED = "completed"
RETRYABLE_FAILED = "retryable_failed"
TERMINAL_FAILED = "terminal_failed"

# A worker may retry these; the work itself is still sound.
RETRYABLE_CODES = frozenset(
    {
        "provider_unavailable",
        "usage_ledger_unavailable",
        "database_unavailable",
        "cross_check_unexpected",
    }
)
# These will fail again no matter how many times we try.
TERMINAL_CODES = frozenset(
    {
        "model_price_missing",
        "provider_configuration_error",
        "invalid_secondary_output",
        "source_missing",
    }
)


@dataclass(frozen=True)
class ClaimedCrossCheck:
    id: int
    score_id: int
    secondary_model: str
    prompt_version: str
    lease_token: UUID
    attempts: int


def _merge_reasons(existing: Any, incoming: list[str]) -> list[str]:
    current = set(existing if isinstance(existing, list) else [])
    current.update(incoming)
    return [reason for reason in TRIGGER_ORDER if reason in current]


async def _greatest_id(db: AsyncSession, score_id: int) -> int | None:
    return (
        await db.execute(
            select(func.max(ScoreCrossCheck.id)).where(
                ScoreCrossCheck.score_id == score_id
            )
        )
    ).scalar_one_or_none()


async def ensure_cross_check(
    db: AsyncSession,
    *,
    score_id: int,
    secondary_model: str,
    prompt_version: str,
    reasons: list[str],
    threshold: Decimal,
) -> ScoreCrossCheck:
    """Queue one check per (score, model, prompt), merging reasons if it exists.

    Caller owns the commit so the trigger can be atomic with whatever caused it.
    """
    previous_greatest = await _greatest_id(db, score_id)

    await db.execute(
        pg_insert(ScoreCrossCheck)
        .values(
            score_id=score_id,
            secondary_model=secondary_model,
            prompt_version=prompt_version,
            sample_reasons=reasons,
            state=QUEUED,
            attempts=0,
            threshold_snapshot=threshold,
        )
        .on_conflict_do_nothing(constraint="uq_cross_checks_score_model_prompt")
    )
    row = (
        await db.execute(
            select(ScoreCrossCheck)
            .where(
                ScoreCrossCheck.score_id == score_id,
                ScoreCrossCheck.secondary_model == secondary_model,
                ScoreCrossCheck.prompt_version == prompt_version,
            )
            .with_for_update()
        )
    ).scalar_one()

    merged = _merge_reasons(row.sample_reasons, reasons)
    if merged != row.sample_reasons:
        row.sample_reasons = merged

    if previous_greatest is None or row.id > previous_greatest:
        # A newly configured check supersedes the old answer, so the projected
        # verdict must not linger while the new one is still pending.
        score = await db.get(Score, score_id, with_for_update=True)
        if score is not None:
            score.cross_engine_diff = None
            score.is_suspicious = False
    await db.flush()
    return row


async def claim_cross_check(
    db: AsyncSession, *, row_id: int, now: datetime, lease_seconds: int, max_attempts: int
) -> ClaimedCrossCheck | None:
    """Take ownership of a queued row, or return None if it is not ours to take."""
    row = (
        await db.execute(
            select(ScoreCrossCheck)
            .where(ScoreCrossCheck.id == row_id, ScoreCrossCheck.state == QUEUED)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None or row.attempts >= max_attempts:
        return None

    token = uuid4()
    row.state = RUNNING
    row.attempts += 1
    row.lease_token = token
    row.lease_expires_at = now + timedelta(seconds=lease_seconds)
    await db.flush()
    return ClaimedCrossCheck(
        id=row.id,
        score_id=row.score_id,
        secondary_model=row.secondary_model,
        prompt_version=row.prompt_version,
        lease_token=token,
        attempts=row.attempts,
    )


async def complete_cross_check(
    db: AsyncSession,
    *,
    row_id: int,
    lease_token: UUID,
    secondary_total: Decimal,
    secondary_dimensions: list[dict[str, Any]],
    now: datetime,
) -> bool:
    """Record a finished check and project it only if it is still the current one."""
    row = (
        await db.execute(
            select(ScoreCrossCheck)
            .where(
                ScoreCrossCheck.id == row_id,
                ScoreCrossCheck.state == RUNNING,
                ScoreCrossCheck.lease_token == lease_token,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        return False

    score = await db.get(Score, row.score_id, with_for_update=True)
    if score is None:
        return False

    diff = abs(Decimal(str(score.total_score)) - secondary_total)
    row.state = COMPLETED
    row.secondary_total_score = secondary_total
    row.secondary_dimensions = secondary_dimensions
    row.absolute_diff = diff
    row.completed_at = now
    row.lease_token = None
    row.lease_expires_at = None

    # A superseded check stays as history; only the newest configuration is
    # allowed to speak for the score.
    if row.id == await _greatest_id(db, row.score_id):
        score.cross_engine_diff = float(diff)
        score.is_suspicious = diff >= row.threshold_snapshot
    await db.flush()
    return True


async def fail_cross_check(
    db: AsyncSession,
    *,
    row_id: int,
    lease_token: UUID,
    error_code: str,
    max_attempts: int,
) -> bool:
    row = (
        await db.execute(
            select(ScoreCrossCheck)
            .where(
                ScoreCrossCheck.id == row_id,
                ScoreCrossCheck.state == RUNNING,
                ScoreCrossCheck.lease_token == lease_token,
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        return False

    retryable = error_code in RETRYABLE_CODES and row.attempts < max_attempts
    row.state = RETRYABLE_FAILED if retryable else TERMINAL_FAILED
    row.last_error_code = error_code
    row.lease_token = None
    row.lease_expires_at = None
    await db.flush()
    return True


async def sweep_cross_checks(
    db: AsyncSession, *, now: datetime, max_attempts: int
) -> list[int]:
    """Requeue recoverable work; give up on rows that exhausted their attempts."""
    expired_or_failed = (
        await db.execute(
            select(ScoreCrossCheck)
            .where(
                (ScoreCrossCheck.state == RETRYABLE_FAILED)
                | (
                    (ScoreCrossCheck.state == RUNNING)
                    & (ScoreCrossCheck.lease_expires_at < now)
                )
            )
            .order_by(ScoreCrossCheck.id)
            .with_for_update(skip_locked=True)
        )
    ).scalars()

    requeued: list[int] = []
    for row in expired_or_failed:
        if row.attempts >= max_attempts:
            row.state = TERMINAL_FAILED
            row.last_error_code = row.last_error_code or "cross_check_unexpected"
        else:
            row.state = QUEUED
            requeued.append(row.id)
        row.lease_token = None
        row.lease_expires_at = None
    await db.flush()
    return requeued
