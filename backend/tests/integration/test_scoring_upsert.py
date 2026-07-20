import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from backend.app.models import JD, Candidate, RuleVersion
from backend.app.scoring.llm_judge import JudgeResult
from backend.app.scoring.pipeline import ScoringPipeline
from backend.app.services.parser.pii import compute_pii_hash, encrypt_pii

FIXTURE = Path(__file__).parents[1] / "fixtures" / "sample_rule_v1.json"

pytestmark = pytest.mark.integration


@pytest.fixture
async def seeded_candidate_and_jd(db_session, monkeypatch):
    """Seed a JD with an active rule version and a scoreable candidate.

    Mirrors the seeding in test_candidates_api.py::test_score_endpoint_returns_total.
    The rule fixture has non-empty judge_dimensions, so LLMJudge.score is
    monkeypatched to avoid a real LLM call (same pattern as test_p2_e2e.py).
    """
    rule_data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    jd = JD(code="FOREIGN_TRADE", name="外贸业务", description="", status="active")
    db_session.add(jd)
    await db_session.flush()
    rv = RuleVersion(
        jd_id=jd.id,
        version="v1",
        schema_json=rule_data,
        published_at=datetime.now(tz=timezone.utc),
        notes="test",
    )
    db_session.add(rv)
    await db_session.flush()
    jd.active_rule_version_id = rv.id

    cand = Candidate(
        source="upload",
        name_cipher=encrypt_pii("张三"),
        pii_hash=compute_pii_hash(name="张三", phone="13800001234"),
        parsed_markdown="独立负责北美 五金",
        extracted_json={
            "age": 30,
            "education": "本科",
            "experiences": [
                {
                    "title": "外贸",
                    "description": "北美 五金 全流程报关、订舱、单证",
                    "start": "2019-01",
                    "end": "2024-01",
                }
            ],
        },
    )
    db_session.add(cand)
    await db_session.commit()

    monkeypatch.setattr(
        "backend.app.scoring.pipeline.LLMJudge.score",
        AsyncMock(
            return_value=JudgeResult(
                dimensions=[],
                model="mock",
                tokens=0,
                prompt_version="resume_judge_v1",
            )
        ),
    )

    return cand.id, jd.id


async def test_rescoring_same_rule_version_is_idempotent(db_session, seeded_candidate_and_jd):
    candidate_id, jd_id = seeded_candidate_and_jd
    first = await ScoringPipeline(db=db_session).run(candidate_id=candidate_id, jd_id=jd_id)
    await db_session.commit()
    second = await ScoringPipeline(db=db_session).run(candidate_id=candidate_id, jd_id=jd_id)
    await db_session.commit()
    assert second.score_id == first.score_id
