from __future__ import annotations

import asyncio
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import get_settings
from backend.app.database import AsyncSessionLocal, engine
from backend.app.models import JD, AuditLog, Candidate, IngestionJob
from backend.app.scoring.pipeline import ScoringPipeline
from backend.app.services.ingestion.states import IngestionState, InvalidTransitionError
from backend.app.services.parser.extractor import ExtractedResume, ResumeExtractor
from backend.app.services.parser.mineru_client import MinerUClient
from backend.app.services.parser.pii import compute_pii_hash, encrypt_pii
from backend.app.services.storage.minio_client import ObjectNotFoundError
from backend.app.services.storage.resume_storage import (
    ResumeStorageService,
    StorageIntegrityError,
    StoredResume,
)
from backend.app.tasks.celery_app import celery_app

logger = structlog.get_logger(__name__)

_CONTENT_TYPE_SUFFIXES = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "image/png": ".png",
    "image/jpeg": ".jpg",
}

# WP2 exception classification for the async worker path. Retryable errors are
# transport/availability failures that may succeed on a later attempt (the
# sweeper re-queues up to INGESTION_MAX_ATTEMPTS); terminal errors indicate the
# input or configuration itself is unusable and retrying will not help.
RETRYABLE_ERRORS: dict[str, str] = {
    "MinerUUnavailableError": "resume_parser_unavailable",
    "LLMUnavailableError": "ai_service_unavailable",
}
TERMINAL_ERRORS: dict[str, str] = {
    "MinerUContractError": "resume_parser_contract_invalid",
    "MinerUTaskError": "resume_parser_failed",
    "LLMConfigurationError": "ai_service_configuration_invalid",
    "LLMInvalidResponseError": "ai_invalid_output",
    "LLMInvalidOutputError": "ai_invalid_output",
}
_GENERIC_RETRYABLE_ERROR_CODE = "ingestion_worker_error"


class CandidateFileConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class RawFileReference:
    object_key: str
    sha256: str
    size_bytes: int
    content_type: str
    original_name_cipher: str

    @property
    def stored_resume(self) -> StoredResume:
        return StoredResume(
            object_key=self.object_key,
            sha256=self.sha256,
            size_bytes=self.size_bytes,
            content_type=self.content_type,
        )


@dataclass(frozen=True)
class IngestionResult:
    candidate_id: int
    status: str


async def _insert_or_reuse_candidate(
    db: AsyncSession,
    raw_file: RawFileReference,
    parsed_markdown: str,
    extracted: ExtractedResume,
    storage: ResumeStorageService,
    *,
    source: str,
    source_external_id: str | None,
) -> tuple[Candidate, str, bool]:
    """Insert a new candidate keyed by `pii_hash`, or reuse an existing one.

    The `INSERT ... ON CONFLICT DO NOTHING` is the actual dedup invariant
    (enforced by Postgres); the branch below is only a fast path to react to
    a conflict when it happens, mirroring the sha256 pattern in
    `IngestionJobService.create_or_reuse`. On a duplicate, the just-uploaded
    raw file object is redundant — an existing candidate already owns the
    canonical copy — so it is deleted here.

    Returns `(candidate, status, object_deleted)`. `status` is `"parsed"`
    for a newly inserted candidate or `"duplicate"` for a reused one.
    `object_deleted` is `True` only when the duplicate branch successfully
    deleted the now-redundant raw file object — callers that also perform
    failure-path object compensation (e.g. `run_parse_and_score`) use this
    to avoid a double-delete attempt.
    """
    pii_hash = compute_pii_hash(name=extracted.name, phone=extracted.phone)
    stmt = (
        pg_insert(Candidate)
        .values(
            source=source,
            source_external_id=source_external_id,
            name_cipher=encrypt_pii(extracted.name or "未知"),
            phone_cipher=encrypt_pii(extracted.phone),
            email_cipher=encrypt_pii(extracted.email),
            raw_file_key=raw_file.object_key,
            raw_file_sha256=raw_file.sha256,
            raw_file_size_bytes=raw_file.size_bytes,
            raw_file_content_type=raw_file.content_type,
            raw_file_original_name_cipher=raw_file.original_name_cipher,
            parsed_markdown=parsed_markdown,
            extracted_json={
                "age": extracted.age,
                "education": extracted.education,
                "experiences": [
                    experience.model_dump() for experience in extracted.experiences
                ],
                "_meta": {
                    "schema_version": extracted.schema_version,
                    "prompt_version": extracted.prompt_version,
                    "model": extracted.model,
                    "tokens": extracted.raw_tokens,
                },
            },
            pii_hash=pii_hash,
        )
        .on_conflict_do_nothing(index_elements=["pii_hash"])
        .returning(Candidate.id)
    )
    inserted_id = (await db.execute(stmt)).scalar_one_or_none()
    status = "parsed"
    object_deleted = False
    if inserted_id is None:
        cand = (
            await db.execute(select(Candidate).where(Candidate.pii_hash == pii_hash))
        ).scalar_one()
        existing = _stored_resume_from_candidate(cand)
        try:
            await storage.verify(existing)
        except (ObjectNotFoundError, StorageIntegrityError) as exc:
            raise CandidateFileConflict(
                "Existing candidate raw file is not verifiable"
            ) from exc
        await storage.delete(raw_file.object_key)
        object_deleted = True
        status = "duplicate"
    else:
        cand = (
            await db.execute(select(Candidate).where(Candidate.id == inserted_id))
        ).scalar_one()
    return cand, status, object_deleted


async def run_parse_and_score(
    *,
    db: AsyncSession,
    local_file_path: str,
    raw_file: RawFileReference,
    storage: ResumeStorageService,
    source: str,
    source_external_id: str | None,
    jd_code: str | None,
    actor: str = "system",
) -> IngestionResult:
    owns_new_object = True
    try:
        parser = MinerUClient()
        parsed = await parser.parse(Path(local_file_path))
        extractor = ResumeExtractor()
        extracted = await extractor.extract(parsed.markdown)

        cand, status, object_deleted = await _insert_or_reuse_candidate(
            db,
            raw_file,
            parsed.markdown,
            extracted,
            storage,
            source=source,
            source_external_id=source_external_id,
        )
        if object_deleted:
            owns_new_object = False

        if jd_code:
            jd = (
                await db.execute(select(JD).where(JD.code == jd_code))
            ).scalar_one_or_none()
            if jd and jd.active_rule_version_id:
                await ScoringPipeline(db=db).run(candidate_id=cand.id, jd_id=jd.id)

        trace_id = structlog.contextvars.get_contextvars().get("trace_id")
        db.add(
            AuditLog(
                event_type="candidate_upload" if status == "parsed" else "candidate_duplicate",
                actor=actor,
                target_type="candidate",
                target_id=cand.id,
                payload={
                    "status": status,
                    "object_key": raw_file.object_key,
                    "sha256": raw_file.sha256,
                    "size_bytes": raw_file.size_bytes,
                    "content_type": raw_file.content_type,
                    "trace_id": trace_id,
                },
            )
        )
        await db.commit()
        owns_new_object = False
        return IngestionResult(candidate_id=cand.id, status=status)
    except (Exception, asyncio.CancelledError) as exc:
        rollback_error: BaseException | None = None
        try:
            await asyncio.shield(db.rollback())
        except BaseException as cleanup_exc:  # includes repeated cancellation
            rollback_error = cleanup_exc

        object_cleanup_error: BaseException | None = None
        if owns_new_object:
            try:
                await asyncio.shield(storage.delete(raw_file.object_key))
            except BaseException as cleanup_exc:  # includes repeated cancellation
                object_cleanup_error = cleanup_exc
                logger.critical(
                    "raw_file_cleanup_failed",
                    trace_id=structlog.contextvars.get_contextvars().get("trace_id"),
                    object_key=raw_file.object_key,
                    sha256=raw_file.sha256,
                    error_type=type(cleanup_exc).__name__,
                )
        if object_cleanup_error is not None:
            raise object_cleanup_error from exc
        if rollback_error is not None:
            raise rollback_error from exc
        raise


def _stored_resume_from_candidate(candidate: Candidate) -> StoredResume:
    if (
        not candidate.raw_file_key
        or not candidate.raw_file_sha256
        or candidate.raw_file_size_bytes is None
        or not candidate.raw_file_content_type
        or not candidate.raw_file_original_name_cipher
    ):
        raise CandidateFileConflict("Existing candidate raw file is not verifiable")
    return StoredResume(
        object_key=candidate.raw_file_key,
        sha256=candidate.raw_file_sha256,
        size_bytes=candidate.raw_file_size_bytes,
        content_type=candidate.raw_file_content_type,
    )


def _reference_from_job(job: IngestionJob) -> RawFileReference:
    return RawFileReference(
        object_key=job.raw_file_key,
        sha256=job.raw_file_sha256,
        size_bytes=job.raw_file_size_bytes,
        content_type=job.raw_file_content_type,
        original_name_cipher=job.raw_file_original_name_cipher,
    )


def _temp_local_path(content_type: str) -> Path:
    suffix = _CONTENT_TYPE_SUFFIXES.get(content_type, ".bin")
    with tempfile.NamedTemporaryFile(
        prefix="smartscreen-worker-", suffix=suffix, delete=False
    ) as temporary:
        return Path(temporary.name)


async def run_job(*, db: AsyncSession, job: IngestionJob, storage: ResumeStorageService) -> None:
    """Drive a claimed ingestion job through parsing, extraction, and scoring.

    `job` must already be claimed (state `parsing`, lease set) by
    `IngestionJobService.claim` — this function does not claim it. Each
    state transition is committed separately from the external call
    (MinerU parse, LLM extract, LLM judge) that follows it, so a crash
    mid-call leaves the job in a processing state with a live lease that
    the sweeper can reclaim and retry.

    Unlike `run_parse_and_score`, this function does NOT delete the MinIO
    object on a general processing failure — the sweeper retries the job
    against the same stored object. The object is only deleted inside
    `_insert_or_reuse_candidate`'s duplicate branch, because that object is
    genuinely redundant (an existing candidate already owns the canonical
    copy), not because the job failed.
    """
    from backend.app.services.ingestion.jobs import IngestionJobService

    svc = IngestionJobService(db)
    reference = _reference_from_job(job)
    local_path = _temp_local_path(reference.content_type)
    try:
        await storage.download_verified(reference.stored_resume, local_path)
        parsed = await MinerUClient().parse(local_path)

        await svc.transition(job, IngestionState.EXTRACTING)
        await db.commit()

        extracted = await ResumeExtractor().extract(parsed.markdown)

        candidate, _status, _object_deleted = await _insert_or_reuse_candidate(
            db,
            reference,
            parsed.markdown,
            extracted,
            storage,
            source=job.source,
            source_external_id=job.source_external_id,
        )
        job.candidate_id = candidate.id

        jd = None
        if job.jd_code:
            jd = (
                await db.execute(select(JD).where(JD.code == job.jd_code))
            ).scalar_one_or_none()

        if jd is not None and jd.active_rule_version_id:
            await svc.transition(job, IngestionState.SCORING)
            await db.commit()

            result = await ScoringPipeline(db=db).run(candidate_id=candidate.id, jd_id=jd.id)
            job.score_id = result.score_id

            await svc.transition(job, IngestionState.COMPLETED)
            await db.commit()
            return

        await svc.transition(job, IngestionState.READY)
        await db.commit()
    finally:
        local_path.unlink(missing_ok=True)


async def _fail_job(job_id: int, exc: BaseException) -> None:
    """Classify a `run_job` failure and transition the job accordingly.

    Opens a fresh session — the caller's session was just rolled back and
    may be in a broken state. Logs metadata only (job id, resulting state,
    error class, trace id); never the exception message body, a provider
    response, or PII.
    """
    from backend.app.services.ingestion.jobs import IngestionJobService

    error_class = type(exc).__name__
    if error_class in TERMINAL_ERRORS:
        target = IngestionState.TERMINAL_FAILED
        error_code = TERMINAL_ERRORS[error_class]
    elif error_class in RETRYABLE_ERRORS:
        target = IngestionState.RETRYABLE_FAILED
        error_code = RETRYABLE_ERRORS[error_class]
    else:
        target = IngestionState.RETRYABLE_FAILED
        error_code = _GENERIC_RETRYABLE_ERROR_CODE

    trace_id = structlog.contextvars.get_contextvars().get("trace_id")
    async with AsyncSessionLocal() as db:
        job = await db.get(IngestionJob, job_id)
        if job is None:
            logger.error(
                "ingestion_job_fail_missing",
                job_id=job_id,
                error_class=error_class,
                trace_id=trace_id,
            )
            return
        try:
            await IngestionJobService(db).transition(job, target, error_code=error_code)
        except InvalidTransitionError:
            await db.rollback()
            logger.error(
                "ingestion_job_fail_invalid_transition",
                job_id=job_id,
                state=job.state,
                target=target.value,
                error_class=error_class,
                trace_id=trace_id,
            )
            return
        await db.commit()
        logger.warning(
            "ingestion_job_failed",
            job_id=job_id,
            state=target.value,
            error_code=error_code,
            error_class=error_class,
            trace_id=trace_id,
        )


@celery_app.task(name="ingest.parse_and_score")
def parse_and_score_task(job_id: int) -> None:
    async def _runner() -> None:
        from backend.app.services.ingestion.jobs import IngestionJobService

        try:
            async with AsyncSessionLocal() as db:
                job = await IngestionJobService(db).claim(
                    job_id, lease_seconds=get_settings().INGESTION_LEASE_SECONDS
                )
                await db.commit()
                if job is None:
                    return
                try:
                    await run_job(db=db, job=job, storage=ResumeStorageService())
                except BaseException as exc:  # noqa: BLE001 - classified below, re-raised
                    await db.rollback()
                    await _fail_job(job_id, exc)
                    raise
        finally:
            await engine.dispose()

    asyncio.run(_runner())


def serialize_raw_file(reference: RawFileReference) -> dict:
    return asdict(reference)
