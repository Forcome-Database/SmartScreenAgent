import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


XLSX = Path(__file__).parents[3] / "招聘JD整理-智能筛简历.xlsx"


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not XLSX.exists(), reason="xlsx missing")
async def test_full_p2_flow(client, db_session, monkeypatch):
    """E2E happy path: import rules → upload resume → score → expect positive total + grade."""
    from datetime import datetime, timezone

    from backend.app.models import JD, RuleVersion
    from backend.app.rules.excel_importer import import_workbook
    from backend.app.services.parser.extractor import ExtractedResume, Experience
    from backend.app.services.parser.mineru_client import ParseResult

    # Mock all external dependencies: MinerU stub, ResumeExtractor, LLMJudge
    monkeypatch.setenv("MINERU_MODE", "stub")
    parser_stub = SimpleNamespace(
        parse=AsyncMock(
            return_value=ParseResult(
                markdown="# r\n张三 北美 五金", layout={}, source="stub"
            )
        )
    )
    extractor_stub = SimpleNamespace(
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
                        title="外贸业务",
                        description="北美 五金 报关 订舱 单证 独立负责",
                        start="2019-01",
                        end="2024-01",
                    )
                ],
            )
        )
    )
    monkeypatch.setattr("backend.app.tasks.ingest.MinerUClient", lambda: parser_stub)
    monkeypatch.setattr("backend.app.tasks.ingest.ResumeExtractor", lambda: extractor_stub)
    monkeypatch.setattr(
        "backend.app.scoring.pipeline.LLMJudge.score",
        AsyncMock(return_value={"dimensions": [], "model": "mock", "tokens": 0}),
    )

    # 1. Import Excel → direct DB insert (bypass CLI)
    rules = import_workbook(XLSX)
    ft = next(r for r in rules if r.jd_code == "FOREIGN_TRADE")
    jd = JD(code="FOREIGN_TRADE", name="外贸业务", description="", status="active")
    db_session.add(jd)
    await db_session.flush()
    rv = RuleVersion(
        jd_id=jd.id,
        version="v1",
        schema_json=ft.model_dump(),
        published_at=datetime.now(tz=timezone.utc),
    )
    db_session.add(rv)
    await db_session.flush()
    jd.active_rule_version_id = rv.id
    await db_session.commit()

    # 2. Upload resume (synchronous 200 "parsed")
    resp = await client.post(
        "/api/v1/candidates/upload",
        files={"file": ("r.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert resp.status_code == 200, resp.text
    cid = resp.json()["candidate_id"]

    # 3. Score
    resp = await client.post(
        f"/api/v1/candidates/{cid}/score", json={"jd_code": "FOREIGN_TRADE"}
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "total_score" in data
    assert data["rejected"] is False


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not XLSX.exists(), reason="xlsx missing")
async def test_p2_hard_filter_rejection(client, db_session, monkeypatch):
    """Same happy path but candidate age=60, should trigger AGE hard filter rejection."""
    from datetime import datetime, timezone

    from sqlalchemy import select

    from backend.app.models import JD, AuditLog, RuleVersion
    from backend.app.rules.excel_importer import import_workbook
    from backend.app.services.parser.extractor import ExtractedResume
    from backend.app.services.parser.mineru_client import ParseResult

    monkeypatch.setenv("MINERU_MODE", "stub")
    monkeypatch.setattr(
        "backend.app.tasks.ingest.MinerUClient",
        lambda: SimpleNamespace(
            parse=AsyncMock(
                return_value=ParseResult(markdown="x", layout={}, source="stub")
            )
        ),
    )
    monkeypatch.setattr(
        "backend.app.tasks.ingest.ResumeExtractor",
        lambda: SimpleNamespace(
            extract=AsyncMock(
                return_value=ExtractedResume(
                    name="老张",
                    phone="13800001234",
                    email=None,
                    education="本科",
                    age=60,
                    experiences=[],
                )
            )
        ),
    )

    rules = import_workbook(XLSX)
    ft = next(r for r in rules if r.jd_code == "FOREIGN_TRADE")
    jd = JD(code="FOREIGN_TRADE", name="外贸业务", description="", status="active")
    db_session.add(jd)
    await db_session.flush()
    rv = RuleVersion(
        jd_id=jd.id,
        version="v1",
        schema_json=ft.model_dump(),
        published_at=datetime.now(tz=timezone.utc),
    )
    db_session.add(rv)
    await db_session.flush()
    jd.active_rule_version_id = rv.id
    await db_session.commit()

    resp = await client.post(
        "/api/v1/candidates/upload",
        files={"file": ("r.pdf", b"%PDF-1.4 fake", "application/pdf")},
        params={"jd_code": "FOREIGN_TRADE"},
    )
    assert resp.status_code == 200, resp.text

    # The upload route uses its own session via `get_db` and commits there.
    # Expire our session so the next query reloads audit rows from the DB.
    db_session.expire_all()
    audits = (
        await db_session.execute(
            select(AuditLog).where(AuditLog.event_type == "hard_filter_reject")
        )
    ).scalars().all()
    assert any(a.payload.get("audit_tag") == "AGE" for a in audits), (
        f"expected AGE audit, got: {[a.payload for a in audits]}"
    )
