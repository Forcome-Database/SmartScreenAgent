from __future__ import annotations

import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.database import AsyncSessionLocal, engine
from backend.app.models import JD, AuditLog, Candidate
from backend.app.scoring.pipeline import ScoringPipeline
from backend.app.services.parser.extractor import ResumeExtractor
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
                parsed_markdown=parsed.markdown,
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
            owns_new_object = False
            status = "duplicate"
        else:
            cand = (
                await db.execute(select(Candidate).where(Candidate.id == inserted_id))
            ).scalar_one()

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
    except Exception as exc:
        await db.rollback()
        if owns_new_object:
            try:
                await storage.delete(raw_file.object_key)
            except Exception as cleanup_exc:
                logger.critical(
                    "raw_file_cleanup_failed",
                    trace_id=structlog.contextvars.get_contextvars().get("trace_id"),
                    object_key=raw_file.object_key,
                    sha256=raw_file.sha256,
                    error_type=type(cleanup_exc).__name__,
                )
                raise cleanup_exc from exc
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


@celery_app.task(name="ingest.parse_and_score")
def parse_and_score_task(
    raw_file: dict,
    source: str,
    source_external_id: str | None,
    jd_code: str | None,
) -> int:
    import asyncio

    async def _runner() -> int:
        try:
            reference = RawFileReference(**raw_file)
            storage = ResumeStorageService()
            suffix = {
                "application/pdf": ".pdf",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
                "image/png": ".png",
                "image/jpeg": ".jpg",
            }.get(reference.content_type, ".bin")
            with tempfile.NamedTemporaryFile(
                prefix="smartscreen-worker-", suffix=suffix, delete=False
            ) as temporary:
                local_path = Path(temporary.name)
            try:
                await storage.download_verified(reference.stored_resume, local_path)
                async with AsyncSessionLocal() as db:
                    result = await run_parse_and_score(
                        db=db,
                        local_file_path=str(local_path),
                        raw_file=reference,
                        storage=storage,
                        source=source,
                        source_external_id=source_external_id,
                        jd_code=jd_code,
                    )
                    return result.candidate_id
            finally:
                local_path.unlink(missing_ok=True)
        finally:
            await engine.dispose()

    return asyncio.run(_runner())


def serialize_raw_file(reference: RawFileReference) -> dict:
    return asdict(reference)
