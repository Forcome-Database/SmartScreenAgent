from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.database import AsyncSessionLocal
from backend.app.models import JD, Candidate
from backend.app.scoring.pipeline import ScoringPipeline
from backend.app.services.parser.extractor import ResumeExtractor
from backend.app.services.parser.mineru_client import MinerUClient
from backend.app.services.parser.pii import compute_pii_hash, encrypt_pii
from backend.app.tasks.celery_app import celery_app


async def run_parse_and_score(
    *,
    db: AsyncSession,
    file_path: str,
    source: str,
    source_external_id: str | None,
    jd_code: str | None,
) -> int:
    parser = MinerUClient()
    parsed = await parser.parse(Path(file_path))
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
            raw_file_key=file_path,
            parsed_markdown=parsed.markdown,
            extracted_json={
                "age": extracted.age,
                "education": extracted.education,
                "experiences": [e.__dict__ for e in extracted.experiences],
            },
            pii_hash=pii_hash,
        )
        .on_conflict_do_nothing(index_elements=["pii_hash"])
    )
    try:
        await db.execute(stmt)
        await db.commit()
    except IntegrityError:
        await db.rollback()
    cand = (
        await db.execute(select(Candidate).where(Candidate.pii_hash == pii_hash))
    ).scalar_one()

    if jd_code:
        jd = (
            await db.execute(select(JD).where(JD.code == jd_code))
        ).scalar_one_or_none()
        if jd and jd.active_rule_version_id:
            await ScoringPipeline(db=db).run(candidate_id=cand.id, jd_id=jd.id)
    return cand.id


@celery_app.task(name="ingest.parse_and_score")
def parse_and_score_task(
    file_path: str, source: str, source_external_id: str | None, jd_code: str | None
) -> int:
    import asyncio

    async def _runner() -> int:
        async with AsyncSessionLocal() as db:
            return await run_parse_and_score(
                db=db,
                file_path=file_path,
                source=source,
                source_external_id=source_external_id,
                jd_code=jd_code,
            )

    return asyncio.run(_runner())
