from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import IngestionJob
from backend.app.services.ingestion.states import (
    TERMINAL_STATES,
    IngestionState,
    assert_transition,
)
from backend.app.tasks.ingest import RawFileReference

_TERMINAL_VALUES = tuple(s.value for s in TERMINAL_STATES)


class IngestionJobService:
    def __init__(self, db: AsyncSession, clock: Callable[[], datetime] | None = None) -> None:
        self.db = db
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    async def create_or_reuse(
        self,
        *,
        raw_file: RawFileReference,
        source: str,
        source_external_id: str | None,
        jd_code: str | None,
        actor: str,
        batch_id: UUID | None = None,
        trace_id: str | None = None,
    ) -> tuple[IngestionJob, bool]:
        existing = (
            await self.db.execute(
                select(IngestionJob)
                .where(IngestionJob.raw_file_sha256 == raw_file.sha256)
                .where(IngestionJob.state.notin_(_TERMINAL_VALUES))
                .limit(1)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing, False
        job = IngestionJob(
            batch_id=batch_id,
            state=IngestionState.QUEUED.value,
            source=source,
            source_external_id=source_external_id,
            jd_code=jd_code,
            raw_file_key=raw_file.object_key,
            raw_file_sha256=raw_file.sha256,
            raw_file_size_bytes=raw_file.size_bytes,
            raw_file_content_type=raw_file.content_type,
            raw_file_original_name_cipher=raw_file.original_name_cipher,
            attempts=0,
            actor=actor,
            trace_id=trace_id,
        )
        self.db.add(job)
        await self.db.flush()
        return job, True

    async def claim(self, job_id: int, *, lease_seconds: int) -> IngestionJob | None:
        job = (
            await self.db.execute(
                select(IngestionJob)
                .where(IngestionJob.id == job_id)
                .where(IngestionJob.state == IngestionState.QUEUED.value)
                .with_for_update(skip_locked=True)
            )
        ).scalar_one_or_none()
        if job is None:
            return None
        assert_transition(IngestionState(job.state), IngestionState.PARSING)
        job.state = IngestionState.PARSING.value
        job.attempts += 1
        job.lease_expires_at = self.clock() + timedelta(seconds=lease_seconds)
        await self.db.flush()
        return job

    async def transition(
        self, job: IngestionJob, target: IngestionState, *, error_code: str | None = None
    ) -> None:
        assert_transition(IngestionState(job.state), target)
        job.state = target.value
        if error_code is not None:
            job.last_error_code = error_code
        if target not in {
            IngestionState.PARSING,
            IngestionState.EXTRACTING,
            IngestionState.SCORING,
        }:
            job.lease_expires_at = None
        await self.db.flush()

    async def batch_counts(self, batch_id: UUID) -> dict[str, int]:
        rows = (
            await self.db.execute(
                select(IngestionJob.state, func.count())
                .where(IngestionJob.batch_id == batch_id)
                .group_by(IngestionJob.state)
            )
        ).all()
        return {state: count for state, count in rows}
