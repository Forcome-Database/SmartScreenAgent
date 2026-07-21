from __future__ import annotations

from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import Settings, get_settings
from backend.app.database import get_db
from backend.app.deps import require_roles
from backend.app.models import JD, IngestionJob, User
from backend.app.scoring.pipeline import ScoringPipeline
from backend.app.security.crypto import encrypt_pii
from backend.app.services.ingestion.jobs import IngestionJobService
from backend.app.services.llm.errors import (
    LLMConfigurationError,
    LLMInvalidOutputError,
    LLMInvalidResponseError,
    LLMUnavailableError,
)
from backend.app.services.parser.errors import (
    MinerUContractError,
    MinerUTaskError,
    MinerUUnavailableError,
)
from backend.app.services.storage import ResumeStorageService, StorageError, StoredResume
from backend.app.services.upload import UploadValidationError, UploadValidator, get_malware_scanner
from backend.app.tasks.ingest import RawFileReference

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/candidates", tags=["candidates"])
WRITE_ROLES = ("hr", "hr_lead", "admin")


class UploadResponse(BaseModel):
    job_id: int
    batch_id: str | None = None
    state: str = "queued"


def _upload_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
    )


def _external_service_error(exc: Exception) -> HTTPException | None:
    if isinstance(exc, MinerUUnavailableError):
        return _upload_error(
            503, "resume_parser_unavailable", "Resume parser is unavailable"
        )
    if isinstance(exc, MinerUContractError):
        return _upload_error(
            502, "resume_parser_contract_invalid", "Resume parser response is invalid"
        )
    if isinstance(exc, MinerUTaskError):
        return _upload_error(502, "resume_parser_failed", "Resume parser failed")
    if isinstance(exc, LLMUnavailableError):
        return _upload_error(503, "ai_service_unavailable", "AI service is unavailable")
    if isinstance(exc, LLMConfigurationError):
        return _upload_error(
            502,
            "ai_service_configuration_invalid",
            "AI service configuration is invalid",
        )
    if isinstance(exc, (LLMInvalidResponseError, LLMInvalidOutputError)):
        return _upload_error(502, "ai_invalid_output", "AI service output is invalid")
    return None


def enqueue_job(job_id: int) -> None:
    """Hand a queued ingestion job off to the Celery worker.

    A thin, module-level wrapper so tests can monkeypatch enqueuing without
    touching Celery. The import is deferred to keep `backend.app.tasks.ingest`
    (and its Celery task registration) out of this module's import-time
    dependency graph.
    """
    from backend.app.tasks.ingest import parse_and_score_task

    parse_and_score_task.delay(job_id)


async def _process_one_file(
    file: UploadFile,
    *,
    jd_code: str | None,
    actor: str,
    batch_id: UUID | None,
    db: AsyncSession,
    settings: Settings,
) -> tuple[IngestionJob, bool]:
    """Validate, scan, store, and create/reuse an ingestion job for one file.

    Shared by `upload_resume` (single) and `upload_batch` (once per file) so
    both routes fail identically for a given file: on success the job row is
    committed and `(job, created)` is returned. On ANY failure — validation,
    storage, or otherwise — the DB session is rolled back and, if this
    file's object was already stored in MinIO, that object is deleted
    before the exception is re-raised (mirrors the `owns_new_object`
    compensating-delete pattern in
    `backend.app.tasks.ingest.run_parse_and_score`). The caller decides how
    to map the propagated exception to a response.
    """
    artifact = await UploadValidator().validate(file)
    storage: ResumeStorageService | None = None
    stored: StoredResume | None = None
    object_needs_cleanup = False
    try:
        await get_malware_scanner(settings.MALWARE_SCAN_MODE).scan(artifact)
        original_name_cipher = encrypt_pii(artifact.original_filename)
        storage = ResumeStorageService()
        stored = await storage.store(artifact)
        object_needs_cleanup = True
        raw_file = RawFileReference(
            object_key=stored.object_key,
            sha256=stored.sha256,
            size_bytes=stored.size_bytes,
            content_type=stored.content_type,
            original_name_cipher=original_name_cipher,
        )
        job, created = await IngestionJobService(db).create_or_reuse(
            raw_file=raw_file,
            source="upload",
            source_external_id=None,
            jd_code=jd_code,
            actor=actor,
            batch_id=batch_id,
            trace_id=structlog.contextvars.get_contextvars().get("trace_id"),
        )
        if not created:
            # Idempotent resubmission of the same sha256: an active job for
            # this file already exists, so the object just stored above is
            # redundant — drop it rather than leaving two copies in MinIO.
            await storage.delete(stored.object_key)
            object_needs_cleanup = False
        await db.commit()
        object_needs_cleanup = False
        return job, created
    except Exception:
        await db.rollback()
        if object_needs_cleanup and storage is not None and stored is not None:
            try:
                await storage.delete(stored.object_key)
            except StorageError as cleanup_exc:
                logger.critical(
                    "raw_file_cleanup_failed",
                    trace_id=structlog.contextvars.get_contextvars().get("trace_id"),
                    object_key=stored.object_key,
                    sha256=stored.sha256,
                    error_type=type(cleanup_exc).__name__,
                )
        raise
    finally:
        artifact.cleanup()


@router.post("/upload", response_model=UploadResponse, status_code=202)
async def upload_resume(
    file: UploadFile = File(...),
    jd_code: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(*WRITE_ROLES)),
) -> UploadResponse:
    """Validate, persist, and enqueue a resume for asynchronous processing.

    Parsing, extraction, and scoring no longer happen inline here — they run
    in the Celery worker (`backend.app.tasks.ingest.parse_and_score_task`).
    This endpoint only validates the upload, scans it, stores the object in
    MinIO, and creates (or reuses, via sha256 idempotency) the ingestion job
    row before returning `202`. The work is delegated to `_process_one_file`
    (shared with `/batch`); unlike `/batch`, a validation or storage failure
    here is still surfaced directly as the mapped HTTP error, not as a
    `terminal_failed` result.
    """
    settings = get_settings()
    try:
        job, created = await _process_one_file(
            file,
            jd_code=jd_code,
            actor=f"user:{current_user.id}",
            batch_id=None,
            db=db,
            settings=settings,
        )
    except UploadValidationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    except StorageError as exc:
        raise _upload_error(
            503, "object_storage_unavailable", "Resume storage is unavailable"
        ) from exc
    finally:
        await file.close()

    if created:
        enqueue_job(job.id)
    return UploadResponse(
        job_id=job.id,
        batch_id=str(job.batch_id) if job.batch_id else None,
        state=job.state,
    )


class BatchJobResult(BaseModel):
    job_id: int | None = None
    state: str
    error_code: str | None = None


class BatchResponse(BaseModel):
    batch_id: str
    jobs: list[BatchJobResult]


@router.post("/batch", response_model=BatchResponse, status_code=202)
async def upload_batch(
    files: list[UploadFile] = File(...),
    jd_code: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(*WRITE_ROLES)),
) -> BatchResponse:
    """Validate, persist, and enqueue multiple resumes under one shared batch.

    Each file is handled independently via `_process_one_file`: ANY failure
    for one file — validation, storage unavailability, or any other
    unexpected error — records a `terminal_failed` result (with a stable
    error_code only, never the raw exception body) for that file only and
    does not abort the rest of the batch. `_process_one_file` commits and
    compensates per file, so an earlier file's already-stored object is
    never orphaned by a later file's failure. Only successfully created
    jobs are enqueued, once every file has been processed.
    """
    settings = get_settings()
    if len(files) > settings.INGESTION_BATCH_MAX_FILES:
        raise _upload_error(413, "batch_too_large", "Too many files in one batch")

    batch_id = uuid4()
    results: list[BatchJobResult] = []
    to_enqueue: list[int] = []

    for file in files:
        try:
            job, created = await _process_one_file(
                file,
                jd_code=jd_code,
                actor=f"user:{current_user.id}",
                batch_id=batch_id,
                db=db,
                settings=settings,
            )
            if created:
                to_enqueue.append(job.id)
            results.append(BatchJobResult(job_id=job.id, state=job.state))
        except UploadValidationError as exc:
            results.append(BatchJobResult(state="terminal_failed", error_code=exc.code))
        except StorageError:
            results.append(
                BatchJobResult(
                    state="terminal_failed", error_code="object_storage_unavailable"
                )
            )
        except Exception as exc:
            logger.error(
                "batch_file_ingestion_failed",
                trace_id=structlog.contextvars.get_contextvars().get("trace_id"),
                error_type=type(exc).__name__,
            )
            results.append(
                BatchJobResult(state="terminal_failed", error_code="ingestion_failed")
            )
        finally:
            await file.close()

    for job_id in to_enqueue:
        enqueue_job(job_id)
    return BatchResponse(batch_id=str(batch_id), jobs=results)


@router.get("/jobs/{job_id}")
async def get_job(
    job_id: int,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_roles(*WRITE_ROLES)),
) -> dict:
    job = await db.get(IngestionJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return {
        "state": job.state,
        "attempts": job.attempts,
        "last_error_code": job.last_error_code,
        "candidate_id": job.candidate_id,
        "score_id": job.score_id,
        "batch_id": str(job.batch_id) if job.batch_id else None,
    }


@router.get("/batches/{batch_id}")
async def get_batch(
    batch_id: str,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_roles(*WRITE_ROLES)),
) -> dict:
    try:
        parsed = UUID(batch_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="batch not found") from exc
    counts = await IngestionJobService(db).batch_counts(parsed)
    if not counts:
        raise HTTPException(status_code=404, detail="batch not found")
    return {"total": sum(counts.values()), "by_state": counts}


class ScoreRequest(BaseModel):
    jd_code: str


class ScoreResponse(BaseModel):
    score_id: int
    total_score: float
    grade: str
    rejected: bool


@router.post("/{candidate_id}/score", response_model=ScoreResponse)
async def score_candidate(
    candidate_id: int,
    payload: ScoreRequest,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_roles(*WRITE_ROLES)),
) -> ScoreResponse:
    jd = (
        await db.execute(select(JD).where(JD.code == payload.jd_code))
    ).scalar_one_or_none()
    if not jd:
        raise HTTPException(status_code=404, detail=f"JD {payload.jd_code} not found")
    try:
        result = await ScoringPipeline(db=db).run(candidate_id=candidate_id, jd_id=jd.id)
        await db.commit()
    except (
        LLMUnavailableError,
        LLMConfigurationError,
        LLMInvalidResponseError,
        LLMInvalidOutputError,
    ) as exc:
        await db.rollback()
        mapped = _external_service_error(exc)
        assert mapped is not None
        raise mapped from exc
    except Exception:
        await db.rollback()
        raise
    return ScoreResponse(
        score_id=result.score_id,
        total_score=result.total_score,
        grade=result.grade,
        rejected=result.rejected,
    )
