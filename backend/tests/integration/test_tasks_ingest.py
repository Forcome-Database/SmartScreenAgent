from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from backend.app.models import Candidate
from backend.app.services.parser.extractor import ExtractedResume, Experience
from backend.app.services.parser.mineru_client import ParseResult


@pytest.mark.integration
@pytest.mark.asyncio
async def test_run_parse_and_score_persists_candidate(db_session, monkeypatch, tmp_path):
    from backend.app.tasks.ingest import run_parse_and_score

    pdf = tmp_path / "resume.pdf"
    pdf.write_bytes(b"%PDF-1.4")

    parser_stub = SimpleNamespace(
        parse=AsyncMock(
            return_value=ParseResult(
                markdown="# resume\n张三 13800001234", layout={}, source="stub"
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
                        title="外贸",
                        description="北美 五金",
                        start="2020-01",
                        end="2024-01",
                    )
                ],
            )
        )
    )
    monkeypatch.setattr("backend.app.tasks.ingest.MinerUClient", lambda: parser_stub)
    monkeypatch.setattr("backend.app.tasks.ingest.ResumeExtractor", lambda: extractor_stub)

    candidate_id = await run_parse_and_score(
        db=db_session,
        file_path=str(pdf),
        source="upload",
        source_external_id=None,
        jd_code=None,
    )
    c = (
        await db_session.execute(select(Candidate).where(Candidate.id == candidate_id))
    ).scalar_one()
    assert c.parsed_markdown.startswith("# resume")
    assert c.extracted_json["age"] == 30
    assert c.name_cipher  # encrypted, non-empty
    assert c.pii_hash and len(c.pii_hash) == 64
