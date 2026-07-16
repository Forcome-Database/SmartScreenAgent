from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import get_settings
from backend.app.database import get_db
from backend.app.deps import require_roles
from backend.app.models import JD, User
from backend.app.scoring.pipeline import ScoringPipeline
from backend.app.security.crypto import encrypt_pii
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
from backend.app.services.storage import ResumeStorageService, StorageError
from backend.app.services.upload import UploadValidationError, UploadValidator, get_malware_scanner
from backend.app.tasks.ingest import (
    CandidateFileConflict,
    RawFileReference,
    run_parse_and_score,
)

router = APIRouter(prefix="/api/v1/candidates", tags=["candidates"])
WRITE_ROLES = ("hr", "hr_lead", "admin")


class UploadResponse(BaseModel):
    candidate_id: int
    status: str = "parsed"


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


@router.post("/upload", response_model=UploadResponse, status_code=200)
async def upload_resume(
    file: UploadFile = File(...),
    jd_code: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(*WRITE_ROLES)),
) -> UploadResponse:
    """P2: 同步解析+抽取（1000份/月 体量足够）；P3 钉钉同步任务一起切到 Celery 异步队列."""
    artifact = None
    try:
        settings = get_settings()
        artifact = await UploadValidator().validate(file)
        await get_malware_scanner(settings.MALWARE_SCAN_MODE).scan(artifact)
        original_name_cipher = encrypt_pii(artifact.original_filename)
        storage = ResumeStorageService()
        stored = await storage.store(artifact)
        raw_file = RawFileReference(
            object_key=stored.object_key,
            sha256=stored.sha256,
            size_bytes=stored.size_bytes,
            content_type=stored.content_type,
            original_name_cipher=original_name_cipher,
        )
        result = await run_parse_and_score(
            db=db,
            local_file_path=str(artifact.path),
            raw_file=raw_file,
            storage=storage,
            source="upload",
            source_external_id=None,
            jd_code=jd_code,
            actor=f"user:{current_user.id}",
        )
    except UploadValidationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    except CandidateFileConflict as exc:
        raise _upload_error(
            409,
            "candidate_file_conflict",
            "Existing candidate raw file is unavailable",
        ) from exc
    except (
        MinerUUnavailableError,
        MinerUContractError,
        MinerUTaskError,
        LLMUnavailableError,
        LLMConfigurationError,
        LLMInvalidResponseError,
        LLMInvalidOutputError,
    ) as exc:
        mapped = _external_service_error(exc)
        assert mapped is not None
        raise mapped from exc
    except StorageError as exc:
        raise _upload_error(
            503,
            "object_storage_unavailable",
            "Resume storage is unavailable",
        ) from exc
    finally:
        if artifact is not None:
            artifact.cleanup()
        await file.close()
    return UploadResponse(candidate_id=result.candidate_id, status=result.status)


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
    except Exception as exc:
        await db.rollback()
        mapped = _external_service_error(exc)
        if mapped is not None:
            raise mapped from exc
        raise
    return ScoreResponse(
        score_id=result.score_id,
        total_score=result.total_score,
        grade=result.grade,
        rejected=result.rejected,
    )
