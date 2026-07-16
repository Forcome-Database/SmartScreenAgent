import hashlib
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from backend.app.models import Candidate
from backend.app.services.parser.extractor import Experience, ExtractedResume
from backend.app.services.parser.mineru_client import ParseResult


@pytest.mark.integration
@pytest.mark.asyncio
async def test_run_parse_and_score_persists_candidate(
    db_session, minio_storage, monkeypatch, tmp_path
):
    from backend.app.security.crypto import encrypt_pii
    from backend.app.services.storage.resume_storage import ResumeStorageService
    from backend.app.tasks.ingest import RawFileReference, run_parse_and_score

    pdf = tmp_path / "resume.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    object_key = "resumes/2026/07/task-ingest.pdf"
    sha256 = hashlib.sha256(pdf.read_bytes()).hexdigest()
    minio_storage.put_object(
        object_key,
        io.BytesIO(pdf.read_bytes()),
        pdf.stat().st_size,
        content_type="application/pdf",
        metadata={"sha256": sha256},
    )

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

    result = await run_parse_and_score(
        db=db_session,
        local_file_path=str(pdf),
        raw_file=RawFileReference(
            object_key=object_key,
            sha256=sha256,
            size_bytes=pdf.stat().st_size,
            content_type="application/pdf",
            original_name_cipher=encrypt_pii("resume.pdf"),
        ),
        storage=ResumeStorageService(storage=minio_storage),
        source="upload",
        source_external_id=None,
        jd_code=None,
    )
    c = (
        await db_session.execute(
            select(Candidate).where(Candidate.id == result.candidate_id)
        )
    ).scalar_one()
    assert result.status == "parsed"
    assert c.parsed_markdown.startswith("# resume")
    assert c.extracted_json["age"] == 30
    assert c.name_cipher  # encrypted, non-empty
    assert c.pii_hash and len(c.pii_hash) == 64
    assert c.raw_file_key == object_key
    assert c.raw_file_sha256 == sha256


@pytest.mark.integration
@pytest.mark.asyncio
async def test_celery_task_downloads_verified_object(
    db_session, celery_worker, minio_storage, monkeypatch
):
    from backend.app.security.crypto import encrypt_pii
    from backend.app.tasks.ingest import (
        RawFileReference,
        parse_and_score_task,
        serialize_raw_file,
    )

    body = b"%PDF-worker-input"
    sha256 = hashlib.sha256(body).hexdigest()
    object_key = "resumes/2026/07/celery-input"
    minio_storage.put_object(
        object_key,
        io.BytesIO(body),
        len(body),
        content_type="application/pdf",
        metadata={"sha256": sha256},
    )
    monkeypatch.setattr(
        "backend.app.tasks.ingest.MinerUClient",
        lambda: SimpleNamespace(
            parse=AsyncMock(
                return_value=ParseResult(
                    markdown="# worker resume", layout={}, source="stub"
                )
            )
        ),
    )
    monkeypatch.setattr(
        "backend.app.tasks.ingest.ResumeExtractor",
        lambda: SimpleNamespace(
            extract=AsyncMock(
                return_value=ExtractedResume(
                    name="Worker Candidate",
                    phone="13900000000",
                    email=None,
                    education="本科",
                    age=28,
                    experiences=[],
                )
            )
        ),
    )
    reference = RawFileReference(
        object_key=object_key,
        sha256=sha256,
        size_bytes=len(body),
        content_type="application/pdf",
        original_name_cipher=encrypt_pii("worker.pdf"),
    )

    task_result = parse_and_score_task.delay(
        serialize_raw_file(reference), "upload", None, None
    )
    try:
        candidate_id = task_result.get(timeout=15)
    finally:
        task_result.forget()

    candidate = (
        await db_session.execute(
            select(Candidate).where(Candidate.id == candidate_id)
        )
    ).scalar_one()
    assert candidate.raw_file_key == object_key
    assert candidate.parsed_markdown == "# worker resume"
