from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from backend.app.config import get_settings
from backend.app.database import AsyncSessionLocal, engine
from backend.app.services.ingestion.sweeper import sweep
from backend.app.tasks.celery_app import celery_app


@celery_app.task(name="ingest.sweep")
def sweep_task() -> dict:
    async def _runner() -> dict:
        settings = get_settings()
        try:
            async with AsyncSessionLocal() as db:
                report = await sweep(
                    db, now=datetime.now(timezone.utc), max_attempts=settings.INGESTION_MAX_ATTEMPTS
                )
                await db.commit()
        finally:
            await engine.dispose()
        for job_id in report.requeued:
            celery_app.send_task("ingest.parse_and_score", args=[job_id])
        return {
            "reclaimed": report.reclaimed,
            "requeued": len(report.requeued),
            "terminated": report.terminated,
        }

    return asyncio.run(_runner())
