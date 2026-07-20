import hashlib
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from backend.app.models import Candidate, IngestionJob
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
            return_value=ParseResult(markdown="# resume\n张三 13800001234", source="stub")
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
                raw_tokens=321,
                model="extract-model",
                prompt_version="resume_extract_v1",
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
        await db_session.execute(select(Candidate).where(Candidate.id == result.candidate_id))
    ).scalar_one()
    assert result.status == "parsed"
    assert c.parsed_markdown.startswith("# resume")
    assert c.extracted_json["age"] == 30
    assert c.extracted_json["_meta"] == {
        "schema_version": 1,
        "prompt_version": "resume_extract_v1",
        "model": "extract-model",
        "tokens": 321,
    }
    assert c.name_cipher  # encrypted, non-empty
    assert c.pii_hash and len(c.pii_hash) == 64
    assert c.raw_file_key == object_key
    assert c.raw_file_sha256 == sha256


@pytest.mark.integration
@pytest.mark.asyncio
async def test_celery_task_downloads_verified_object(
    db_session, celery_worker, minio_storage, monkeypatch
):
    from backend.app.database import AsyncSessionLocal, engine
    from backend.app.security.crypto import encrypt_pii
    from backend.app.services.ingestion.jobs import IngestionJobService
    from backend.app.services.ingestion.states import IngestionState
    from backend.app.tasks.ingest import RawFileReference, parse_and_score_task

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
            parse=AsyncMock(return_value=ParseResult(markdown="# worker resume", source="stub"))
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

    svc = IngestionJobService(db_session)
    job, _ = await svc.create_or_reuse(
        raw_file=reference,
        source="upload",
        source_external_id=None,
        jd_code=None,
        actor="user:1",
    )
    await db_session.commit()

    # `db_session` and the celery worker's own per-task session share the
    # same module-level async engine/pool (celery_worker runs the worker
    # in-process for tests, in a background thread with its own event loop
    # via `asyncio.run()`; production runs it as a separate process with its
    # own engine, so this hazard does not exist there). asyncpg connections
    # are bound to the event loop that created them, and `pool_pre_ping=True`
    # pings a checked-out connection on every checkout — so a connection
    # this commit() just returned to the pool (bound to the main
    # pytest-asyncio loop) would crash the worker with "attached to a
    # different loop" if its own loop picked it up. Dispose the pool now,
    # before `.delay()` hands the job to that thread, so the worker always
    # opens a connection bound to its own loop.
    await engine.dispose()

    # The worker (celery_worker fixture) claims and processes the job via
    # its own DB session/connection; `.get(timeout=...)` blocks until that
    # session has committed, so the row is visible once it returns.
    task_result = parse_and_score_task.delay(job.id)
    try:
        task_result.get(timeout=15)
    finally:
        task_result.forget()

    # Read back with a fresh session/connection rather than reusing
    # `db_session` — its connection was disposed above, and the worker
    # thread's own end-of-task `engine.dispose()` (from a different loop)
    # can otherwise leave this session's greenlet-bridged state unusable.
    async with AsyncSessionLocal() as verify_db:
        refreshed = await verify_db.get(IngestionJob, job.id)
        assert refreshed.state == IngestionState.READY.value
        assert refreshed.candidate_id is not None

        candidate = (
            await verify_db.execute(
                select(Candidate).where(Candidate.id == refreshed.candidate_id)
            )
        ).scalar_one()
        assert candidate.raw_file_key == object_key
        assert candidate.parsed_markdown == "# worker resume"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_run_job_completes_with_stub_parser(db_session, minio_storage, monkeypatch):
    """A claimed job with no `jd_code` reaches `ready` and backfills `candidate_id`."""
    from backend.app.security.crypto import encrypt_pii
    from backend.app.services.ingestion.jobs import IngestionJobService
    from backend.app.services.ingestion.states import IngestionState
    from backend.app.services.storage.resume_storage import ResumeStorageService
    from backend.app.tasks.ingest import RawFileReference, run_job

    body = b"%PDF-run-job-ready"
    sha256 = hashlib.sha256(body).hexdigest()
    object_key = "resumes/2026/07/run-job-ready"
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
            parse=AsyncMock(return_value=ParseResult(markdown="# run_job resume", source="stub"))
        ),
    )
    monkeypatch.setattr(
        "backend.app.tasks.ingest.ResumeExtractor",
        lambda: SimpleNamespace(
            extract=AsyncMock(
                return_value=ExtractedResume(
                    name="Run Job Candidate",
                    phone="13800009999",
                    email=None,
                    education="本科",
                    age=31,
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
        original_name_cipher=encrypt_pii("run-job.pdf"),
    )

    svc = IngestionJobService(db_session)
    job, _ = await svc.create_or_reuse(
        raw_file=reference,
        source="upload",
        source_external_id=None,
        jd_code=None,
        actor="user:1",
    )
    await db_session.commit()
    claimed = await svc.claim(job.id, lease_seconds=900)
    await db_session.commit()

    await run_job(db=db_session, job=claimed, storage=ResumeStorageService(storage=minio_storage))

    refreshed = await db_session.get(IngestionJob, job.id)
    assert refreshed.state == IngestionState.READY.value
    assert refreshed.candidate_id is not None
    assert refreshed.score_id is None

    candidate = (
        await db_session.execute(
            select(Candidate).where(Candidate.id == refreshed.candidate_id)
        )
    ).scalar_one()
    assert candidate.raw_file_key == object_key
    assert candidate.parsed_markdown == "# run_job resume"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_run_job_completes_scoring_when_jd_has_active_rule_version(
    db_session, minio_storage, monkeypatch
):
    """A claimed job with a `jd_code` that has an active rule version reaches
    `completed` and backfills both `candidate_id` and `score_id`."""
    import json
    from datetime import datetime, timezone
    from pathlib import Path

    from backend.app.models import JD, RuleVersion
    from backend.app.scoring.llm_judge import JudgeResult
    from backend.app.security.crypto import encrypt_pii
    from backend.app.services.ingestion.jobs import IngestionJobService
    from backend.app.services.ingestion.states import IngestionState
    from backend.app.services.storage.resume_storage import ResumeStorageService
    from backend.app.tasks.ingest import RawFileReference, run_job

    rule_data = json.loads(
        (Path(__file__).parents[1] / "fixtures" / "sample_rule_v1.json").read_text(
            encoding="utf-8"
        )
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
    await db_session.commit()

    # judge_dimensions is non-empty in the fixture, so LLMJudge.score must be
    # mocked to avoid a real LLM call (same pattern as test_scoring_upsert.py).
    monkeypatch.setattr(
        "backend.app.scoring.pipeline.LLMJudge.score",
        AsyncMock(
            return_value=JudgeResult(
                dimensions=[], model="mock", tokens=0, prompt_version="resume_judge_v1"
            )
        ),
    )

    body = b"%PDF-run-job-scoring"
    sha256 = hashlib.sha256(body).hexdigest()
    object_key = "resumes/2026/07/run-job-scoring"
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
                return_value=ParseResult(markdown="# r\n北美 五金 独立负责", source="stub")
            )
        ),
    )
    monkeypatch.setattr(
        "backend.app.tasks.ingest.ResumeExtractor",
        lambda: SimpleNamespace(
            extract=AsyncMock(
                return_value=ExtractedResume(
                    name="评分候选人",
                    phone="13800001111",
                    email=None,
                    education="本科",
                    age=29,
                    experiences=[
                        Experience(
                            company="X",
                            title="外贸",
                            description="北美 五金 独立负责",
                            start="2020-01",
                            end="2024-01",
                        )
                    ],
                )
            )
        ),
    )
    reference = RawFileReference(
        object_key=object_key,
        sha256=sha256,
        size_bytes=len(body),
        content_type="application/pdf",
        original_name_cipher=encrypt_pii("run-job-score.pdf"),
    )

    svc = IngestionJobService(db_session)
    job, _ = await svc.create_or_reuse(
        raw_file=reference,
        source="upload",
        source_external_id=None,
        jd_code="FOREIGN_TRADE",
        actor="user:1",
    )
    await db_session.commit()
    claimed = await svc.claim(job.id, lease_seconds=900)
    await db_session.commit()

    await run_job(db=db_session, job=claimed, storage=ResumeStorageService(storage=minio_storage))

    refreshed = await db_session.get(IngestionJob, job.id)
    assert refreshed.state == IngestionState.COMPLETED.value
    assert refreshed.candidate_id is not None
    assert refreshed.score_id is not None
