from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.database import AsyncSessionLocal
from backend.app.models import JD, Candidate, RuleVersion, Score, ScoreCrossCheck
from backend.app.security.crypto import encrypt_pii
from backend.app.services.cross_check.state import (
    claim_cross_check,
    complete_cross_check,
    ensure_cross_check,
    fail_cross_check,
    sweep_cross_checks,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

NOW = datetime.now(UTC)
MODEL = "test-secondary"
PROMPT = "resume_judge_v1"
THRESHOLD = Decimal("10")


async def _seed_score(db: AsyncSession, *, total: float = 70) -> Score:
    code = f"CC_{uuid4().hex[:6]}"
    jd = JD(code=code, name=code, description="", status="active")
    db.add(jd)
    await db.flush()
    version = RuleVersion(
        jd_id=jd.id, version="v1", published_at=NOW, schema_json={"jd_code": code}
    )
    db.add(version)
    await db.flush()
    candidate = Candidate(
        source="upload",
        name_cipher=encrypt_pii("private-name"),
        pii_hash=uuid4().hex,
        extracted_json={},
    )
    db.add(candidate)
    await db.flush()
    score = Score(
        candidate_id=candidate.id,
        jd_id=jd.id,
        rule_version_id=version.id,
        total_score=total,
        grade="L1",
        hard_filter_result={},
        rule_dimensions={},
        judge_dimensions={"dimensions": []},
    )
    db.add(score)
    await db.flush()
    return score


async def _ensure(db: AsyncSession, score_id: int, reasons: list[str], **overrides):
    return await ensure_cross_check(
        db,
        score_id=score_id,
        secondary_model=overrides.get("model", MODEL),
        prompt_version=overrides.get("prompt", PROMPT),
        reasons=reasons,
        threshold=THRESHOLD,
    )


async def test_ensure_is_idempotent_and_unions_reasons_in_order(db_session) -> None:
    score = await _seed_score(db_session)

    await _ensure(db_session, score.id, ["low_confidence"])
    row = await _ensure(db_session, score.id, ["deterministic_sample"])
    await db_session.commit()

    assert row.state == "queued"
    assert row.attempts == 0
    assert row.sample_reasons == ["deterministic_sample", "low_confidence"]
    total = (
        await db_session.execute(select(func.count()).select_from(ScoreCrossCheck))
    ).scalar_one()
    assert total == 1


async def test_concurrent_ensures_keep_exactly_one_row(db_session) -> None:
    score = await _seed_score(db_session)
    await db_session.commit()

    async def once(reason: str) -> None:
        async with AsyncSessionLocal() as session:
            await _ensure(session, score.id, [reason])
            await session.commit()

    await asyncio.gather(once("low_confidence"), once("golden_error"))

    total = (
        await db_session.execute(
            select(func.count())
            .select_from(ScoreCrossCheck)
            .where(ScoreCrossCheck.score_id == score.id)
        )
    ).scalar_one()
    assert total == 1


async def test_a_new_configuration_clears_the_previous_projection(db_session) -> None:
    score = await _seed_score(db_session)
    first = await _ensure(db_session, score.id, ["deterministic_sample"])
    claimed = await claim_cross_check(
        db_session, row_id=first.id, now=NOW, lease_seconds=900, max_attempts=3
    )
    assert claimed is not None
    await complete_cross_check(
        db_session,
        row_id=first.id,
        lease_token=claimed.lease_token,
        secondary_total=Decimal("50"),
        secondary_dimensions=[],
        now=NOW,
    )
    await db_session.commit()
    await db_session.refresh(score)
    assert score.is_suspicious is True

    # A different prompt version is a different question; the old verdict goes.
    await _ensure(db_session, score.id, ["deterministic_sample"], prompt="resume_judge_v2")
    await db_session.commit()
    await db_session.refresh(score)

    assert score.cross_engine_diff is None
    assert score.is_suspicious is False


async def test_re_ensuring_the_same_configuration_keeps_a_completed_projection(
    db_session,
) -> None:
    score = await _seed_score(db_session)
    row = await _ensure(db_session, score.id, ["deterministic_sample"])
    claimed = await claim_cross_check(
        db_session, row_id=row.id, now=NOW, lease_seconds=900, max_attempts=3
    )
    assert claimed is not None
    await complete_cross_check(
        db_session,
        row_id=row.id,
        lease_token=claimed.lease_token,
        secondary_total=Decimal("50"),
        secondary_dimensions=[],
        now=NOW,
    )
    await db_session.commit()

    await _ensure(db_session, score.id, ["golden_error"])
    await db_session.commit()
    await db_session.refresh(score)

    assert score.cross_engine_diff == Decimal("20.00")
    assert score.is_suspicious is True


async def test_claim_only_takes_queued_rows_and_respects_max_attempts(db_session) -> None:
    score = await _seed_score(db_session)
    row = await _ensure(db_session, score.id, ["deterministic_sample"])
    await db_session.commit()

    claimed = await claim_cross_check(
        db_session, row_id=row.id, now=NOW, lease_seconds=900, max_attempts=3
    )
    assert claimed is not None and claimed.attempts == 1
    assert claimed.lease_token is not None

    # Already running: a second worker gets nothing.
    assert (
        await claim_cross_check(
            db_session, row_id=row.id, now=NOW, lease_seconds=900, max_attempts=3
        )
        is None
    )

    row.state = "queued"
    row.attempts = 3
    await db_session.flush()
    assert (
        await claim_cross_check(
            db_session, row_id=row.id, now=NOW, lease_seconds=900, max_attempts=3
        )
        is None
    )


async def test_a_stale_worker_cannot_complete_or_fail(db_session) -> None:
    score = await _seed_score(db_session)
    row = await _ensure(db_session, score.id, ["deterministic_sample"])
    claimed = await claim_cross_check(
        db_session, row_id=row.id, now=NOW, lease_seconds=900, max_attempts=3
    )
    assert claimed is not None

    assert not await complete_cross_check(
        db_session,
        row_id=row.id,
        lease_token=uuid4(),
        secondary_total=Decimal("70"),
        secondary_dimensions=[],
        now=NOW,
    )
    assert not await fail_cross_check(
        db_session,
        row_id=row.id,
        lease_token=uuid4(),
        error_code="provider_unavailable",
        max_attempts=3,
    )

    # The rightful owner still succeeds, and a duplicate completion does not.
    assert await complete_cross_check(
        db_session,
        row_id=row.id,
        lease_token=claimed.lease_token,
        secondary_total=Decimal("72"),
        secondary_dimensions=[],
        now=NOW,
    )
    assert not await complete_cross_check(
        db_session,
        row_id=row.id,
        lease_token=claimed.lease_token,
        secondary_total=Decimal("72"),
        secondary_dimensions=[],
        now=NOW,
    )


async def test_completion_stores_diff_and_flags_only_past_the_threshold(
    db_session,
) -> None:
    score = await _seed_score(db_session, total=70)
    row = await _ensure(db_session, score.id, ["deterministic_sample"])
    claimed = await claim_cross_check(
        db_session, row_id=row.id, now=NOW, lease_seconds=900, max_attempts=3
    )
    assert claimed is not None

    await complete_cross_check(
        db_session,
        row_id=row.id,
        lease_token=claimed.lease_token,
        secondary_total=Decimal("65"),
        secondary_dimensions=[{"id": "independence", "score": 5}],
        now=NOW,
    )
    await db_session.commit()
    await db_session.refresh(score)

    assert row.absolute_diff == Decimal("5.00")
    # 5 < threshold 10, so a disagreement this small is not suspicious.
    assert score.is_suspicious is False
    assert score.cross_engine_diff == Decimal("5.00")


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("provider_unavailable", "retryable_failed"),
        ("model_price_missing", "terminal_failed"),
        ("invalid_secondary_output", "terminal_failed"),
    ],
)
async def test_failure_codes_choose_retryable_or_terminal(
    db_session, code: str, expected: str
) -> None:
    score = await _seed_score(db_session)
    row = await _ensure(db_session, score.id, ["deterministic_sample"])
    claimed = await claim_cross_check(
        db_session, row_id=row.id, now=NOW, lease_seconds=900, max_attempts=3
    )
    assert claimed is not None

    await fail_cross_check(
        db_session,
        row_id=row.id,
        lease_token=claimed.lease_token,
        error_code=code,
        max_attempts=3,
    )
    await db_session.commit()

    assert row.state == expected
    assert row.lease_token is None


async def test_sweep_requeues_expired_leases_and_retires_exhausted_rows(
    db_session,
) -> None:
    score = await _seed_score(db_session)
    row = await _ensure(db_session, score.id, ["deterministic_sample"])
    claimed = await claim_cross_check(
        db_session, row_id=row.id, now=NOW, lease_seconds=900, max_attempts=3
    )
    assert claimed is not None
    row.lease_expires_at = NOW - timedelta(minutes=1)
    await db_session.flush()

    requeued = await sweep_cross_checks(db_session, now=NOW, max_attempts=3)
    await db_session.commit()

    assert requeued == [row.id]
    assert row.state == "queued"

    row.state = "retryable_failed"
    row.attempts = 3
    await db_session.flush()
    assert await sweep_cross_checks(db_session, now=NOW, max_attempts=3) == []
    assert row.state == "terminal_failed"


async def test_a_late_completion_cannot_overwrite_the_current_projection(
    db_session,
) -> None:
    score = await _seed_score(db_session, total=70)
    old = await _ensure(db_session, score.id, ["deterministic_sample"])
    old_claim = await claim_cross_check(
        db_session, row_id=old.id, now=NOW, lease_seconds=900, max_attempts=3
    )
    assert old_claim is not None

    # A newer configuration arrives while the old worker is still running.
    new = await _ensure(
        db_session, score.id, ["deterministic_sample"], prompt="resume_judge_v2"
    )
    new_claim = await claim_cross_check(
        db_session, row_id=new.id, now=NOW, lease_seconds=900, max_attempts=3
    )
    assert new_claim is not None
    await complete_cross_check(
        db_session,
        row_id=new.id,
        lease_token=new_claim.lease_token,
        secondary_total=Decimal("69"),
        secondary_dimensions=[],
        now=NOW,
    )

    # The straggler finishes afterwards with a wildly different answer.
    await complete_cross_check(
        db_session,
        row_id=old.id,
        lease_token=old_claim.lease_token,
        secondary_total=Decimal("10"),
        secondary_dimensions=[],
        now=NOW,
    )
    await db_session.commit()
    await db_session.refresh(score)

    # It is recorded as history, but the current verdict stands.
    assert old.absolute_diff == Decimal("60.00")
    assert score.cross_engine_diff == Decimal("1.00")
    assert score.is_suspicious is False
