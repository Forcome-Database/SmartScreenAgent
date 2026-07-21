import hashlib
import io
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from backend.app.models import Candidate, IngestionJob
from backend.app.services.parser.extractor import Experience, ExtractedResume
from backend.app.services.parser.mineru_client import ParseResult


async def _drive_job_to_failure(*, db_session, claimed_job, storage) -> BaseException:
    """Run a claimed job, expect `run_job` to raise, and classify/persist the
    failure via `_fail_job` — the same sequence `parse_and_score_task`'s
    in-process runner performs, without needing a real Celery worker.

    `_fail_job` opens its own session (the caller's `db_session` was just
    rolled back and may not be reusable for further writes), so callers must
    read the resulting state back via a fresh `AsyncSessionLocal()`.
    """
    from backend.app.tasks.ingest import _fail_job, run_job

    with pytest.raises(BaseException) as exc_info:
        await run_job(db=db_session, job=claimed_job, storage=storage)
    await db_session.rollback()
    await _fail_job(claimed_job.id, exc_info.value)
    return exc_info.value


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


@pytest.mark.integration
@pytest.mark.asyncio
async def test_run_job_resumes_from_existing_candidate_without_download(db_session):
    """A retry where `candidate_id` is already set must not re-download the
    raw file object.

    Reproduces the durability gap: the duplicate-candidate branch in
    `_insert_or_reuse_candidate` deletes the just-uploaded MinIO object
    once an existing candidate is confirmed to own the canonical copy. If a
    *later* stage then fails transiently and the sweeper re-queues the job,
    `run_job` re-running from the top would call `download_verified` against
    that now-deleted object and raise `ObjectNotFoundError`, looping the job
    to `terminal_failed` even though the candidate row already exists. The
    fix branches on `job.candidate_id` at the start of `run_job` and skips
    storage/MinerU/extractor entirely on resume — this test proves that by
    never storing a MinIO object for the job's `raw_file_key` at all and
    handing `run_job` a storage stub whose `download_verified` raises if
    called.
    """
    from backend.app.security.crypto import encrypt_pii as encrypt_raw_name
    from backend.app.services.ingestion.jobs import IngestionJobService
    from backend.app.services.ingestion.states import IngestionState
    from backend.app.services.parser.pii import compute_pii_hash, encrypt_pii
    from backend.app.tasks.ingest import RawFileReference, run_job

    # An existing candidate created by a prior (successful) attempt.
    candidate = Candidate(
        source="upload",
        source_external_id=None,
        name_cipher=encrypt_pii("已存在候选人"),
        phone_cipher=encrypt_pii("13800002222"),
        email_cipher=None,
        raw_file_key="resumes/2026/07/resume-already-owned",
        raw_file_sha256=hashlib.sha256(b"already-owned").hexdigest(),
        raw_file_size_bytes=17,
        raw_file_content_type="application/pdf",
        raw_file_original_name_cipher=encrypt_raw_name("already-owned.pdf"),
        parsed_markdown="# already parsed",
        extracted_json={"age": 30, "education": "本科", "experiences": [], "_meta": {}},
        pii_hash=compute_pii_hash(name="已存在候选人", phone="13800002222"),
    )
    db_session.add(candidate)
    await db_session.flush()

    body = b"%PDF-retry-input"
    sha256 = hashlib.sha256(body).hexdigest()
    object_key = "resumes/2026/07/run-job-resume-retry"
    reference = RawFileReference(
        object_key=object_key,
        sha256=sha256,
        size_bytes=len(body),
        content_type="application/pdf",
        original_name_cipher=encrypt_raw_name("retry.pdf"),
    )

    # No MinIO object is ever stored for `object_key` (unlike the other
    # tests in this file) — if `run_job` attempted a download here, it
    # would hit the real `ObjectNotFoundError` path, proving the point on
    # its own. The stub below makes the assertion explicit and immediate.
    svc = IngestionJobService(db_session)
    job, _ = await svc.create_or_reuse(
        raw_file=reference,
        source="upload",
        source_external_id=None,
        jd_code=None,
        actor="user:1",
    )
    job.candidate_id = candidate.id
    await db_session.commit()

    claimed = await svc.claim(job.id, lease_seconds=900)
    await db_session.commit()
    assert claimed.state == IngestionState.PARSING.value

    storage_stub = SimpleNamespace(
        download_verified=AsyncMock(
            side_effect=AssertionError(
                "run_job must not download when job.candidate_id is already set"
            )
        )
    )

    await run_job(db=db_session, job=claimed, storage=storage_stub)

    storage_stub.download_verified.assert_not_called()

    refreshed = await db_session.get(IngestionJob, job.id)
    assert refreshed.state == IngestionState.READY.value
    assert refreshed.candidate_id == candidate.id
    assert refreshed.score_id is None


async def _stored_and_claimed_job(db_session, minio_storage, *, body: bytes, object_key: str):
    """Store a raw object in MinIO and create+claim an `IngestionJob` for it.

    Shared setup for the worker-path failure tests below — mirrors the
    happy-path tests earlier in this file (`test_run_job_completes_with_stub_parser`
    et al.), factored out because every failure test needs the same claimed,
    `parsing`-state job to hand to `run_job`.
    """
    from backend.app.security.crypto import encrypt_pii
    from backend.app.services.ingestion.jobs import IngestionJobService
    from backend.app.tasks.ingest import RawFileReference

    sha256 = hashlib.sha256(body).hexdigest()
    minio_storage.put_object(
        object_key,
        io.BytesIO(body),
        len(body),
        content_type="application/pdf",
        metadata={"sha256": sha256},
    )
    reference = RawFileReference(
        object_key=object_key,
        sha256=sha256,
        size_bytes=len(body),
        content_type="application/pdf",
        original_name_cipher=encrypt_pii("failure-case.pdf"),
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
    return claimed


@pytest.mark.integration
@pytest.mark.asyncio
async def test_run_job_retryable_parser_error_leaves_job_retryable_failed(
    db_session, minio_storage, monkeypatch, caplog
):
    """A `MinerUUnavailableError` from the parser is a retryable failure:
    the job lands in `retryable_failed` with the stable
    `resume_parser_unavailable` code, and the raw exception message (which
    may echo upstream/provider details) never reaches logs or the persisted
    error code."""
    from backend.app.database import AsyncSessionLocal
    from backend.app.services.ingestion.states import IngestionState
    from backend.app.services.parser.errors import MinerUUnavailableError
    from backend.app.services.storage.resume_storage import ResumeStorageService

    secret = "upstream-token-should-never-leak-93f7"
    claimed = await _stored_and_claimed_job(
        db_session,
        minio_storage,
        body=b"%PDF-retryable-parser",
        object_key="resumes/2026/07/retryable-parser",
    )
    monkeypatch.setattr(
        "backend.app.tasks.ingest.MinerUClient",
        lambda: SimpleNamespace(
            parse=AsyncMock(
                side_effect=MinerUUnavailableError(f"provider outage, token={secret}")
            )
        ),
    )

    with caplog.at_level(logging.INFO):
        exc = await _drive_job_to_failure(
            db_session=db_session,
            claimed_job=claimed,
            storage=ResumeStorageService(storage=minio_storage),
        )

    assert isinstance(exc, MinerUUnavailableError)
    assert secret not in caplog.text

    async with AsyncSessionLocal() as verify_db:
        refreshed = await verify_db.get(IngestionJob, claimed.id)
        assert refreshed.state == IngestionState.RETRYABLE_FAILED.value
        assert refreshed.last_error_code == "resume_parser_unavailable"
        assert secret not in refreshed.last_error_code


@pytest.mark.integration
@pytest.mark.asyncio
async def test_run_job_terminal_parser_contract_error_leaves_job_terminal_failed(
    db_session, minio_storage, monkeypatch
):
    """A `MinerUContractError` (the parser returned a response violating the
    supported protocol) is terminal, not retryable: retrying an invalid
    contract will not help."""
    from backend.app.database import AsyncSessionLocal
    from backend.app.services.ingestion.states import IngestionState
    from backend.app.services.parser.errors import MinerUContractError
    from backend.app.services.storage.resume_storage import ResumeStorageService

    claimed = await _stored_and_claimed_job(
        db_session,
        minio_storage,
        body=b"%PDF-terminal-contract",
        object_key="resumes/2026/07/terminal-contract",
    )
    monkeypatch.setattr(
        "backend.app.tasks.ingest.MinerUClient",
        lambda: SimpleNamespace(
            parse=AsyncMock(side_effect=MinerUContractError("unsupported response shape"))
        ),
    )

    exc = await _drive_job_to_failure(
        db_session=db_session,
        claimed_job=claimed,
        storage=ResumeStorageService(storage=minio_storage),
    )
    assert isinstance(exc, MinerUContractError)

    async with AsyncSessionLocal() as verify_db:
        refreshed = await verify_db.get(IngestionJob, claimed.id)
        assert refreshed.state == IngestionState.TERMINAL_FAILED.value
        assert refreshed.last_error_code == "resume_parser_contract_invalid"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_run_job_terminal_llm_invalid_output_leaves_job_terminal_failed(
    db_session, minio_storage, monkeypatch, caplog
):
    """A terminal LLM error (`LLMInvalidOutputError`) raised by the
    extractor — after the parser has already succeeded — must also be
    classified terminal, and the parsed resume text embedded in the
    exception message must not leak into logs or the persisted error
    code."""
    from backend.app.database import AsyncSessionLocal
    from backend.app.services.ingestion.states import IngestionState
    from backend.app.services.llm.errors import LLMInvalidOutputError
    from backend.app.services.storage.resume_storage import ResumeStorageService

    pii_secret = "张三 13800001234 私密简历内容"
    claimed = await _stored_and_claimed_job(
        db_session,
        minio_storage,
        body=b"%PDF-terminal-llm",
        object_key="resumes/2026/07/terminal-llm",
    )
    monkeypatch.setattr(
        "backend.app.tasks.ingest.MinerUClient",
        lambda: SimpleNamespace(
            parse=AsyncMock(return_value=ParseResult(markdown="# resume", source="stub"))
        ),
    )
    monkeypatch.setattr(
        "backend.app.tasks.ingest.ResumeExtractor",
        lambda: SimpleNamespace(
            extract=AsyncMock(
                side_effect=LLMInvalidOutputError(f"model echoed private text: {pii_secret}")
            )
        ),
    )

    with caplog.at_level(logging.INFO):
        exc = await _drive_job_to_failure(
            db_session=db_session,
            claimed_job=claimed,
            storage=ResumeStorageService(storage=minio_storage),
        )

    assert isinstance(exc, LLMInvalidOutputError)
    assert pii_secret not in caplog.text

    async with AsyncSessionLocal() as verify_db:
        refreshed = await verify_db.get(IngestionJob, claimed.id)
        assert refreshed.state == IngestionState.TERMINAL_FAILED.value
        assert refreshed.last_error_code == "ai_invalid_output"
        assert pii_secret not in refreshed.last_error_code


@pytest.mark.integration
@pytest.mark.asyncio
async def test_run_job_unknown_error_defaults_to_retryable_failed(
    db_session, minio_storage, monkeypatch
):
    """An error class `_fail_job` doesn't recognize (neither in
    `TERMINAL_ERRORS` nor `RETRYABLE_ERRORS`) must default to
    `retryable_failed` with the generic stable code — fail safe toward
    "try again" rather than silently discarding an unclassified job."""
    from backend.app.database import AsyncSessionLocal
    from backend.app.services.ingestion.states import IngestionState
    from backend.app.services.storage.resume_storage import ResumeStorageService

    claimed = await _stored_and_claimed_job(
        db_session,
        minio_storage,
        body=b"%PDF-unknown-error",
        object_key="resumes/2026/07/unknown-error",
    )
    monkeypatch.setattr(
        "backend.app.tasks.ingest.MinerUClient",
        lambda: SimpleNamespace(
            parse=AsyncMock(side_effect=RuntimeError("totally unexpected failure"))
        ),
    )

    exc = await _drive_job_to_failure(
        db_session=db_session,
        claimed_job=claimed,
        storage=ResumeStorageService(storage=minio_storage),
    )
    assert isinstance(exc, RuntimeError)

    async with AsyncSessionLocal() as verify_db:
        refreshed = await verify_db.get(IngestionJob, claimed.id)
        assert refreshed.state == IngestionState.RETRYABLE_FAILED.value
        assert refreshed.last_error_code == "ingestion_worker_error"
