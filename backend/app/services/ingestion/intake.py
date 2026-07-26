from __future__ import annotations

from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile

from backend.app.models import IngestionJob
from backend.app.security.crypto import encrypt_pii
from backend.app.services.ingestion.jobs import IngestionJobService
from backend.app.services.storage import ResumeStorageService, StorageError, StoredResume
from backend.app.services.upload.malware import MalwareScanner
from backend.app.services.upload.validation import UploadValidator
from backend.app.tasks.ingest import RawFileReference

logger = structlog.get_logger(__name__)


def enqueue_job(job_id: int) -> None:
    """Hand a queued ingestion job off to the Celery worker.

    A thin, module-level wrapper so every caller enqueues through one name that
    tests can neutralise without touching Celery. The import stays deferred so
    that importing this module never depends on task registration order.
    """
    from backend.app.tasks.ingest import parse_and_score_task

    parse_and_score_task.delay(job_id)


async def ingest_upload(
    file: UploadFile,
    *,
    db: AsyncSession,
    validator: UploadValidator,
    scanner: MalwareScanner,
    storage: ResumeStorageService,
    jobs: IngestionJobService,
    source: str,
    source_external_id: str | None,
    jd_code: str | None,
    actor: str,
    batch_id: UUID | None = None,
    trace_id: str | None = None,
) -> tuple[IngestionJob, bool]:
    """Validate, scan, store, and create/reuse an ingestion job for one file.

    The single way bytes become an ingestion job, whoever supplied them: an HR
    upload and a background sync must fail identically for the same file, so
    neither may own a private copy of this sequence. On success the job row is
    committed and `(job, created)` is returned. On ANY failure — validation,
    malware, storage, or otherwise — the DB session is rolled back and, if this
    file's object was already stored in MinIO, that object is deleted before
    the exception is re-raised (mirrors the `owns_new_object` compensating-
    delete pattern in `backend.app.tasks.ingest.run_parse_and_score`). The
    caller decides how to map the propagated exception.

    Collaborators are injected rather than constructed here so that a caller
    keeps control of which validator, scanner, storage, and job service its own
    request is wired to; both call sites build the same four.
    """
    artifact = await validator.validate(file)
    stored: StoredResume | None = None
    object_needs_cleanup = False
    try:
        await scanner.scan(artifact)
        original_name_cipher = encrypt_pii(artifact.original_filename)
        stored = await storage.store(artifact)
        object_needs_cleanup = True
        raw_file = RawFileReference(
            object_key=stored.object_key,
            sha256=stored.sha256,
            size_bytes=stored.size_bytes,
            content_type=stored.content_type,
            original_name_cipher=original_name_cipher,
        )
        job, created = await jobs.create_or_reuse(
            raw_file=raw_file,
            source=source,
            source_external_id=source_external_id,
            jd_code=jd_code,
            actor=actor,
            batch_id=batch_id,
            trace_id=trace_id,
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
        if object_needs_cleanup and stored is not None:
            try:
                await storage.delete(stored.object_key)
            except StorageError as cleanup_exc:
                logger.critical(
                    "raw_file_cleanup_failed",
                    trace_id=trace_id,
                    object_key=stored.object_key,
                    sha256=stored.sha256,
                    error_type=type(cleanup_exc).__name__,
                )
        raise
    finally:
        artifact.cleanup()
