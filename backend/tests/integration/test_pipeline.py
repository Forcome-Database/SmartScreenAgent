import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from backend.app.models import AuditLog, Candidate, JD, RuleVersion, Score
from backend.app.scoring.pipeline import ScoringPipeline
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

    fake_judge = AsyncMock()
    fake_judge.score.return_value = {
        "dimensions": [
            {
                "id": "independence",
                "tier": "high",
                "score": 10,
                "evidence_quotes": [],
                "reasoning": "ok",
                "confidence": 0.9,
            }
        ],
        "model": "gpt-5.5",
        "tokens": 100,
    }
    pipeline = ScoringPipeline(db=db_session, judge=fake_judge)
    result = await pipeline.run(candidate_id=cand.id, jd_id=jd.id)

    assert result.total_score > 0
    assert result.score_id is not None
    assert not result.rejected

    stored = (
        await db_session.execute(select(Score).where(Score.id == result.score_id))
    ).scalar_one()
    assert stored.rule_version_id == rv.id
    assert not stored.is_suspicious
    assert stored.llm_model_main == "gpt-5.5"

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

    audits = (
        await db_session.execute(
            select(AuditLog).where(AuditLog.event_type == "hard_filter_reject")
        )
    ).scalars().all()
    assert len(audits) == 1
    assert audits[0].payload["audit_tag"] == "AGE"
    assert audits[0].payload["jd_code"] == "X"
