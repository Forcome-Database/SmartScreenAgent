from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import IngestionJob
from backend.app.services.ingestion.states import IngestionState, assert_transition


@dataclass
class SweepReport:
    reclaimed: int = 0
    requeued: list[int] = field(default_factory=list)
    terminated: int = 0


_PROCESSING = tuple(
    s.value for s in (IngestionState.PARSING, IngestionState.EXTRACTING, IngestionState.SCORING)
)


async def sweep(db: AsyncSession, *, now: datetime, max_attempts: int) -> SweepReport:
    """Recover durability by reclaiming dead-worker jobs and retrying/terminating failures.

    Runs in three steps, all against `SELECT ... FOR UPDATE SKIP LOCKED` so it
    is safe to run concurrently with workers claiming jobs:

    1. Reclaim: processing jobs (`parsing`/`extracting`/`scoring`) whose lease
       has expired (the worker holding it is presumed dead) move to
       `retryable_failed`.
    2. Re-enqueue: `retryable_failed` jobs with `attempts < max_attempts` move
       to `queued`; their ids are collected in `requeued` for the caller to
       hand to Celery.
    3. Terminate: `retryable_failed` jobs with `attempts >= max_attempts` move
       to `terminal_failed`.

    Jobs reclaimed in step 1 become `retryable_failed` and are re-evaluated by
    steps 2/3 in this same sweep, so a stuck job already at the attempts cap
    terminates immediately rather than waiting for a second sweep.
    """
    report = SweepReport()

    stuck = (
        (
            await db.execute(
                select(IngestionJob)
                .where(IngestionJob.state.in_(_PROCESSING))
                .where(IngestionJob.lease_expires_at.is_not(None))
                .where(IngestionJob.lease_expires_at < now)
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    for job in stuck:
        assert_transition(IngestionState(job.state), IngestionState.RETRYABLE_FAILED)
        job.state = IngestionState.RETRYABLE_FAILED.value
        job.lease_expires_at = None
        report.reclaimed += 1

    # The session is autoflush=False (see `AsyncSessionLocal`), so the reclaim
    # loop's state changes above are only in-memory until flushed. Flush here
    # so the retryable-jobs query below observes rows reclaimed in step 1
    # within this same sweep/transaction, per the task's requirement that a
    # reclaimed job already at the attempts cap terminates immediately.
    await db.flush()

    retryable = (
        (
            await db.execute(
                select(IngestionJob)
                .where(IngestionJob.state == IngestionState.RETRYABLE_FAILED.value)
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    for job in retryable:
        if job.attempts >= max_attempts:
            assert_transition(IngestionState(job.state), IngestionState.TERMINAL_FAILED)
            job.state = IngestionState.TERMINAL_FAILED.value
            report.terminated += 1
        else:
            assert_transition(IngestionState(job.state), IngestionState.QUEUED)
            job.state = IngestionState.QUEUED.value
            report.requeued.append(job.id)

    await db.flush()
    return report
