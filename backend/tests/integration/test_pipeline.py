import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select

from backend.app.database import AsyncSessionLocal
from backend.app.models import JD, AuditLog, Candidate, LLMUsageAttempt, RuleVersion, Score
from backend.app.scoring.llm_judge import JudgeDimensionResult, JudgeResult, LLMJudge
from backend.app.scoring.pipeline import ScoringPipeline
from backend.app.services.llm.gateway import LLMGateway
from backend.app.services.llm.usage import UsageRecorder
from backend.app.services.parser.pii import compute_pii_hash, encrypt_pii

FIXTURE = Path(__file__).parents[1] / "fixtures" / "sample_rule_v1.json"


pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_pipeline_happy_path(db_session):
    rule_data = json.loads(FIXTURE.read_text(encoding="utf-8"))

    jd = JD(code="FOREIGN_TRADE", name="外贸业务", description="", status="active")
    db_session.add(jd)
    await db_session.flush()
    rv = RuleVersion(
        jd_id=jd.id,
        version="v1",
        schema_json=rule_data,
        notes="test",
        published_at=datetime.now(tz=timezone.utc),
    )
    db_session.add(rv)
    await db_session.flush()
    jd.active_rule_version_id = rv.id

    cand = Candidate(
        source="upload",
        name_cipher=encrypt_pii("张三"),
        phone_cipher=encrypt_pii("13800001234"),
        pii_hash=compute_pii_hash(name="张三", phone="13800001234"),
        parsed_markdown="独立负责美国北美 五金 客户开发",
        extracted_json={
            "age": 30,
            "education": "本科",
            "experiences": [
                {
                    "title": "外贸业务",
                    "description": "北美 五金 全流程报关、订舱、单证",
                    "start": "2019-01",
                    "end": "2024-01",
                }
            ],
        },
    )
    db_session.add(cand)
    await db_session.commit()

    call_group_id = uuid4()
    async def score_without_business_transaction(**_kwargs):
        assert not db_session.in_transaction()
        return JudgeResult(
            dimensions=[
                JudgeDimensionResult(
                    id="independence",
                    tier="high",
                    score=10,
                    evidence_quotes=[],
                    reasoning="ok",
                    confidence=0.9,
                    suggested_interview_questions=[],
                )
            ],
            model="gpt-5.5",
            tokens=100,
            prompt_version="resume_judge_v1",
            call_group_id=call_group_id,
        )

    fake_judge = AsyncMock()
    fake_judge.score.side_effect = score_without_business_transaction
    pipeline = ScoringPipeline(db=db_session, judge=fake_judge)
    result = await pipeline.run(candidate_id=cand.id, jd_id=jd.id)

    assert result.total_score > 0
    assert result.score_id is not None
    assert not result.rejected

    async with AsyncSessionLocal() as other_session:
        committed = (
            await other_session.execute(select(Score).where(Score.id == result.score_id))
        ).scalar_one()
        assert committed.id == result.score_id

    stored = (
        await db_session.execute(select(Score).where(Score.id == result.score_id))
    ).scalar_one()
    assert stored.rule_version_id == rv.id
    assert not stored.is_suspicious
    assert stored.llm_model_main == "gpt-5.5"
    assert stored.llm_judge_call_group_id == call_group_id
    assert fake_judge.score.await_args.kwargs["context"].rule_version_id == rv.id

    # A "score" audit row was written.
    score_audits = (
        await db_session.execute(
            select(AuditLog).where(AuditLog.event_type == "score")
        )
    ).scalars().all()
    assert len(score_audits) == 1
    assert score_audits[0].payload["jd_code"] == "FOREIGN_TRADE"


@pytest.mark.asyncio
async def test_pipeline_hard_filter_rejection_writes_audit(db_session):
    rule_data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    jd = JD(code="X", name="X", description="", status="active")
    db_session.add(jd)
    await db_session.flush()
    rv = RuleVersion(
        jd_id=jd.id,
        version="v1",
        schema_json=rule_data,
        published_at=datetime.now(tz=timezone.utc),
    )
    db_session.add(rv)
    await db_session.flush()
    jd.active_rule_version_id = rv.id

    cand = Candidate(
        source="upload",
        name_cipher=encrypt_pii("老人"),
        pii_hash=compute_pii_hash(name="老人", phone=None),
        parsed_markdown="x",
        extracted_json={"age": 60, "education": "本科", "experiences": []},
    )
    db_session.add(cand)
    await db_session.commit()

    pipeline = ScoringPipeline(db=db_session, judge=AsyncMock())
    result = await pipeline.run(candidate_id=cand.id, jd_id=jd.id)

    assert result.rejected
    assert result.score_id is not None
    assert result.grade == "rejected"

    stored = await db_session.get(Score, result.score_id)
    assert stored.llm_judge_call_group_id is None
    pipeline.judge.score.assert_not_awaited()

    audits = (
        await db_session.execute(
            select(AuditLog).where(AuditLog.event_type == "hard_filter_reject")
        )
    ).scalars().all()
    assert len(audits) == 1
    assert audits[0].payload["audit_tag"] == "AGE"
    assert audits[0].payload["jd_code"] == "X"


@pytest.mark.asyncio
async def test_pipeline_empty_judge_dimensions_persist_null_group(db_session):
    rule_data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    rule_data["judge_dimensions"] = []
    rule_data["total_score"] = 90
    jd = JD(code="NO_JUDGE", name="No Judge", description="", status="active")
    db_session.add(jd)
    await db_session.flush()
    rv = RuleVersion(
        jd_id=jd.id,
        version="v1",
        schema_json=rule_data,
        published_at=datetime.now(tz=timezone.utc),
    )
    db_session.add(rv)
    await db_session.flush()
    jd.active_rule_version_id = rv.id
    cand = Candidate(
        source="upload",
        name_cipher=encrypt_pii("No Judge Candidate"),
        pii_hash=compute_pii_hash(name="No Judge Candidate", phone=None),
        parsed_markdown="resume",
        extracted_json={"age": 30, "education": "本科", "experiences": []},
    )
    db_session.add(cand)
    await db_session.commit()
    gateway = AsyncMock()

    result = await ScoringPipeline(
        db=db_session, judge=LLMJudge(gateway=gateway)
    ).run(candidate_id=cand.id, jd_id=jd.id)

    score = await db_session.get(Score, result.score_id)
    assert score.llm_judge_call_group_id is None
    gateway.judge.assert_not_awaited()


@pytest.mark.asyncio
async def test_pipeline_correlates_terminal_attempts_without_mutating_them(db_session):
    rule_data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    jd = JD(code="METERED", name="Metered", description="", status="active")
    db_session.add(jd)
    await db_session.flush()
    rv = RuleVersion(
        jd_id=jd.id,
        version="v1",
        schema_json=rule_data,
        published_at=datetime.now(tz=timezone.utc),
    )
    db_session.add(rv)
    await db_session.flush()
    jd.active_rule_version_id = rv.id
    cand = Candidate(
        source="upload",
        name_cipher=encrypt_pii("Private Candidate"),
        pii_hash=compute_pii_hash(name="Private Candidate", phone=None),
        parsed_markdown="independent ownership",
        extracted_json={"age": 30, "education": "本科", "experiences": []},
    )
    db_session.add(cand)
    await db_session.commit()

    invalid_payload = {
        "dimensions": [
            {
                "id": "wrong-id",
                "tier": "high",
                "score": 10,
                "evidence_quotes": ["independent ownership"],
                "reasoning": "supported",
                "confidence": 0.9,
                "suggested_interview_questions": [],
            }
        ]
    }
    valid_payload = {
        "dimensions": [
            {
                **invalid_payload["dimensions"][0],
                "id": "independence",
            }
        ]
    }
    provider = AsyncMock(
        side_effect=[
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=json.dumps(invalid_payload))
                    )
                ],
                model="test-judge",
                usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7),
            ),
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=json.dumps(valid_payload))
                    )
                ],
                model="test-judge-fallback",
                usage=SimpleNamespace(prompt_tokens=13, completion_tokens=8),
            ),
        ]
    )
    recorder = UsageRecorder()
    real_begin = recorder.begin

    async def begin_without_business_transaction(**kwargs):
        assert not db_session.in_transaction()
        return await real_begin(**kwargs)

    recorder.begin = AsyncMock(side_effect=begin_without_business_transaction)
    gateway = LLMGateway(recorder=recorder)
    gateway._client.chat.completions.create = provider

    result = await ScoringPipeline(
        db=db_session, judge=LLMJudge(gateway=gateway)
    ).run(candidate_id=cand.id, jd_id=jd.id, trace_id="pipeline-trace")
    committed_score = await db_session.get(Score, result.score_id)
    assert committed_score.llm_judge_call_group_id is not None

    async with AsyncSessionLocal() as verify_db:
        attempts = (
            await verify_db.execute(
                select(LLMUsageAttempt)
                .where(
                    LLMUsageAttempt.call_group_id
                    == committed_score.llm_judge_call_group_id
                )
                .order_by(LLMUsageAttempt.id)
            )
        ).scalars().all()
        assert [row.attempt_role for row in attempts] == ["primary", "fallback"]
        assert [row.status for row in attempts] == ["succeeded", "succeeded"]
        assert all(row.score_id is None for row in attempts)
        terminal_snapshots = [
            (
                row.status,
                row.input_tokens,
                row.output_tokens,
                row.estimated_cost_cny,
                row.finished_at,
            )
            for row in attempts
        ]

    async with AsyncSessionLocal() as verify_db:
        reloaded_score = await verify_db.get(Score, result.score_id)
        assert reloaded_score.llm_judge_call_group_id == attempts[0].call_group_id
        reloaded = (
            await verify_db.execute(
                select(LLMUsageAttempt)
                .where(LLMUsageAttempt.call_group_id == attempts[0].call_group_id)
                .order_by(LLMUsageAttempt.id)
            )
        ).scalars().all()
        assert [
            (
                row.status,
                row.input_tokens,
                row.output_tokens,
                row.estimated_cost_cny,
                row.finished_at,
            )
            for row in reloaded
        ] == terminal_snapshots
        assert all(row.score_id is None for row in reloaded)
    provider.assert_awaited()
