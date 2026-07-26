from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import JD, Candidate, RuleVersion, Score, ScoreCrossCheck
from backend.app.scoring.llm_judge import JudgeDimensionResult, JudgeResult
from backend.app.scoring.pipeline import ScoringPipeline
from backend.app.security.crypto import encrypt_pii

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

FIXTURE = Path(__file__).parents[1] / "fixtures" / "sample_rule_v1.json"
NOW = datetime.now(timezone.utc)


async def _seed(db: AsyncSession) -> tuple[JD, Candidate]:
    code = f"CCT_{uuid4().hex[:6]}"
    schema = json.loads(FIXTURE.read_text(encoding="utf-8"))
    schema["jd_code"] = code
    # Remove the hard filters so scoring always reaches the judge.
    schema["hard_filters"] = []
    jd = JD(code=code, name=code, description="", status="active")
    db.add(jd)
    await db.flush()
    version = RuleVersion(
        jd_id=jd.id, version="v1", published_at=NOW - timedelta(days=1),
        schema_json=schema,
    )
    db.add(version)
    await db.flush()
    jd.active_rule_version_id = version.id
    candidate = Candidate(
        source="upload",
        name_cipher=encrypt_pii("private-name"),
        pii_hash=uuid4().hex,
        extracted_json={"age": 30, "education": "本科", "experiences": []},
        parsed_markdown="北美 五金 resume text",
    )
    db.add(candidate)
    await db.flush()
    await db.commit()
    return jd, candidate


def _judge(confidence: float) -> AsyncMock:
    judge = AsyncMock()
    judge.score.return_value = JudgeResult(
        dimensions=[
            JudgeDimensionResult(
                id="independence",
                tier="high",
                score=10,
                evidence_quotes=["北美 五金"],
                reasoning="ok",
                confidence=confidence,
                suggested_interview_questions=[],
            )
        ],
        model="test-judge",
        tokens=10,
        prompt_version="resume_judge_v1",
        call_group_id=uuid4(),
    )
    return judge


async def test_low_confidence_score_queues_a_check_in_the_same_transaction(
    db_session,
) -> None:
    jd, candidate = await _seed(db_session)

    result = await ScoringPipeline(db=db_session, judge=_judge(0.1)).run(
        candidate_id=candidate.id, jd_id=jd.id
    )

    assert result.cross_check_ids
    row = (
        await db_session.execute(
            select(ScoreCrossCheck).where(ScoreCrossCheck.score_id == result.score_id)
        )
    ).scalar_one()
    assert row.state == "queued"
    assert "low_confidence" in row.sample_reasons
    assert row.threshold_snapshot == Decimal("10.00")


async def test_a_confident_unsampled_score_queues_nothing(db_session, monkeypatch) -> None:
    jd, candidate = await _seed(db_session)
    # Disable the deterministic sample so only confidence can trigger.
    from backend.app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "CROSS_ENGINE_SAMPLE_PERCENT", 0)

    result = await ScoringPipeline(db=db_session, judge=_judge(0.99)).run(
        candidate_id=candidate.id, jd_id=jd.id
    )

    assert result.cross_check_ids == []
    total = (
        await db_session.execute(
            select(func.count())
            .select_from(ScoreCrossCheck)
            .where(ScoreCrossCheck.score_id == result.score_id)
        )
    ).scalar_one()
    assert total == 0


async def test_a_rolled_back_score_leaves_no_queued_check(db_session, monkeypatch) -> None:
    jd, candidate = await _seed(db_session)

    from backend.app.scoring import pipeline as pipeline_module

    original = pipeline_module.ScoringPipeline._maybe_queue_cross_check

    async def queue_then_fail(self, **kwargs):
        await original(self, **kwargs)
        raise RuntimeError("injected failure after queueing")

    monkeypatch.setattr(
        pipeline_module.ScoringPipeline, "_maybe_queue_cross_check", queue_then_fail
    )

    with pytest.raises(RuntimeError):
        await ScoringPipeline(db=db_session, judge=_judge(0.1)).run(
            candidate_id=candidate.id, jd_id=jd.id
        )
    await db_session.rollback()

    assert (
        await db_session.execute(select(func.count()).select_from(ScoreCrossCheck))
    ).scalar_one() == 0
    assert (
        await db_session.execute(select(func.count()).select_from(Score))
    ).scalar_one() == 0


async def test_rescoring_the_same_configuration_is_idempotent(db_session) -> None:
    jd, candidate = await _seed(db_session)

    first = await ScoringPipeline(db=db_session, judge=_judge(0.1)).run(
        candidate_id=candidate.id, jd_id=jd.id
    )
    second = await ScoringPipeline(db=db_session, judge=_judge(0.1)).run(
        candidate_id=candidate.id, jd_id=jd.id
    )

    assert first.score_id == second.score_id
    total = (
        await db_session.execute(select(func.count()).select_from(ScoreCrossCheck))
    ).scalar_one()
    assert total == 1
