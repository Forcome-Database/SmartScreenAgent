from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import IngestionJob
from backend.app.services.ingestion.states import (
    PROCESSING_STATES,
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
        existing = await self._find_active_by_sha256(raw_file.sha256)
        if existing is not None:
            return existing, False

        # The SELECT above is only a fast path: two concurrent requests for the
        # same sha256 can both pass it, so the actual invariant is enforced by
        # the partial unique index (uq_ingestion_jobs_sha256_active) via this
        # atomic upsert, mirroring the pii_hash pattern in tasks/ingest.py.
        stmt = (
            pg_insert(IngestionJob)
            .values(
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
            .on_conflict_do_nothing(
                index_elements=["raw_file_sha256"],
                index_where=IngestionJob.state.notin_(_TERMINAL_VALUES),
            )
            .returning(IngestionJob.id)
        )
        inserted_id = (await self.db.execute(stmt)).scalar_one_or_none()
        if inserted_id is not None:
            job = (
                await self.db.execute(
                    select(IngestionJob).where(IngestionJob.id == inserted_id)
                )
            ).scalar_one()
            await self.db.flush()
            return job, True

        # A concurrent insert won the race; re-read the row it created.
        existing = await self._find_active_by_sha256(raw_file.sha256)
        if existing is None:  # pragma: no cover - defensive, should be unreachable
            raise RuntimeError(
                "ingestion job insert conflicted but no active row was found"
            )
        return existing, False

    async def _find_active_by_sha256(self, sha256: str) -> IngestionJob | None:
        return (
            await self.db.execute(
                select(IngestionJob)
                .where(IngestionJob.raw_file_sha256 == sha256)
                .where(IngestionJob.state.notin_(_TERMINAL_VALUES))
                .limit(1)
            )
        ).scalar_one_or_none()

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
        if target not in PROCESSING_STATES:
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
