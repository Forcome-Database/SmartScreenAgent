from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.database import get_db
from backend.app.models import JD
from backend.app.scoring.pipeline import ScoringPipeline
from backend.app.tasks.ingest import run_parse_and_score

router = APIRouter(prefix="/api/v1/candidates", tags=["candidates"])


class UploadResponse(BaseModel):
    candidate_id: int
    status: str = "parsed"


def _unlink_safe(path: str) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass


@router.post("/upload", response_model=UploadResponse, status_code=200)
async def upload_resume(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    jd_code: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> UploadResponse:
    """P2: 同步解析+抽取（1000份/月 体量足够）；P3 钉钉同步任务一起切到 Celery 异步队列."""
    suffix = Path(file.filename or "resume.pdf").suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name
    background.add_task(_unlink_safe, tmp_path)
    candidate_id = await run_parse_and_score(
        db=db,
        file_path=tmp_path,
        source="upload",
        source_external_id=None,
        jd_code=jd_code,
    )
    return UploadResponse(candidate_id=candidate_id, status="parsed")


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
) -> ScoreResponse:
    jd = (
        await db.execute(select(JD).where(JD.code == payload.jd_code))
    ).scalar_one_or_none()
    if not jd:
        raise HTTPException(status_code=404, detail=f"JD {payload.jd_code} not found")
    result = await ScoringPipeline(db=db).run(candidate_id=candidate_id, jd_id=jd.id)
    return ScoreResponse(
        score_id=result.score_id,
        total_score=result.total_score,
        grade=result.grade,
        rejected=result.rejected,
    )
