from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.app.models import JD, Candidate, RuleVersion
from backend.app.services.parser.extractor import Experience, ExtractedResume
from backend.app.services.parser.mineru_client import ParseResult


@pytest.mark.integration
@pytest.mark.asyncio
async def test_upload_returns_candidate_id(client, db_session, monkeypatch):
    """Upload endpoint synchronously parses + extracts, returns 200 with candidate_id."""
    monkeypatch.setenv("MINERU_MODE", "stub")
    monkeypatch.setattr(
        "backend.app.tasks.ingest.MinerUClient",
        lambda: SimpleNamespace(
            parse=AsyncMock(
                return_value=ParseResult(markdown="# r\n张三", layout={}, source="stub")
            )
        ),
    )
    monkeypatch.setattr(
        "backend.app.tasks.ingest.ResumeExtractor",
        lambda: SimpleNamespace(
            extract=AsyncMock(
                return_value=ExtractedResume(
                    name="张三",
                    phone="13800001234",
                    email=None,
                    education="本科",
                    age=30,
                    experiences=[
                        Experience(
                            company="X",
                            title="外贸",
                            description="北美 五金",
                            start="2020-01",
                            end="2024-01",
                        )
                    ],
                )
            )
        ),
    )
    files = {"file": ("r.pdf", b"%PDF-1.4 dummy", "application/pdf")}
    resp = await client.post("/api/v1/candidates/upload", files=files)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["candidate_id"] is not None
    assert body["status"] == "parsed"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_upload_parser_failure_returns_502(client, db_session, monkeypatch):
    from backend.app.services.parser.mineru_client import MinerUParseError

    monkeypatch.setattr(
        "backend.app.tasks.ingest.MinerUClient",
        lambda: SimpleNamespace(
            parse=AsyncMock(side_effect=MinerUParseError("missing markdown"))
        ),
    )
    resp = await client.post(
        "/api/v1/candidates/upload",
        files={"file": ("r.pdf", b"%PDF-1.4 dummy", "application/pdf")},
    )
    assert resp.status_code == 502
    assert resp.json()["detail"] == "Resume parser failed"
    assert "missing markdown" not in resp.text
    assert "mineru.example.com" not in resp.text
    assert "r.pdf" not in resp.text


@pytest.mark.integration
@pytest.mark.asyncio
async def test_score_endpoint_returns_total(client, db_session, monkeypatch):
    """Score endpoint: given existing candidate + JD with active rule, returns total + grade."""
    import json
    from datetime import datetime, timezone

    rule_data = json.loads(
        (Path(__file__).parents[1] / "fixtures" / "sample_rule_v1.json").read_text(encoding="utf-8")
    )
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

    from backend.app.services.parser.pii import compute_pii_hash, encrypt_pii

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
        AsyncMock(return_value={"dimensions": [], "model": "mock", "tokens": 0}),
    )

    resp = await client.post(
        f"/api/v1/candidates/{cand.id}/score", json={"jd_code": "FOREIGN_TRADE"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "total_score" in body
    assert "grade" in body


@pytest.mark.integration
@pytest.mark.asyncio
async def test_score_endpoint_unknown_jd_returns_404(client, db_session):
    resp = await client.post(
        "/api/v1/candidates/999/score", json={"jd_code": "NONEXISTENT"}
    )
    assert resp.status_code == 404
