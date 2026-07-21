from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from backend.app.models import IngestionJob
from backend.app.services.ingestion.states import IngestionState
from backend.app.services.ingestion.sweeper import sweep

pytestmark = pytest.mark.integration

_QUEUED_STALE_SECONDS = 900  # mirrors the default INGESTION_LEASE_SECONDS


async def test_expired_lease_is_reclaimed_and_requeued(db_session, stored_job_factory):
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    job = await stored_job_factory(state=IngestionState.PARSING, lease_expires_at=past, attempts=1)
    await db_session.commit()
    report = await sweep(
        db_session,
        now=datetime.now(timezone.utc),
        max_attempts=3,
        queued_stale_seconds=_QUEUED_STALE_SECONDS,
    )
    await db_session.commit()
    refreshed = await db_session.get(IngestionJob, job.id)
    assert job.id in report.requeued
    assert refreshed.state == IngestionState.QUEUED.value


async def test_retryable_at_cap_terminates(db_session, stored_job_factory):
    job = await stored_job_factory(state=IngestionState.RETRYABLE_FAILED, attempts=3)
    await db_session.commit()
    report = await sweep(
        db_session,
        now=datetime.now(timezone.utc),
        max_attempts=3,
        queued_stale_seconds=_QUEUED_STALE_SECONDS,
    )
    await db_session.commit()
    refreshed = await db_session.get(IngestionJob, job.id)
    assert refreshed.state == IngestionState.TERMINAL_FAILED.value
    assert report.terminated == 1


async def test_reclaimed_job_already_at_cap_terminates_in_same_sweep(
    db_session, stored_job_factory
):
    """A processing job with an expired lease AND attempts already at the cap
    must be reclaimed and terminated in one sweep, not requeued or left
    waiting for a second sweep."""
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    job = await stored_job_factory(state=IngestionState.SCORING, lease_expires_at=past, attempts=3)
    await db_session.commit()
    report = await sweep(
        db_session,
        now=datetime.now(timezone.utc),
        max_attempts=3,
        queued_stale_seconds=_QUEUED_STALE_SECONDS,
    )
    await db_session.commit()
    refreshed = await db_session.get(IngestionJob, job.id)
    assert refreshed.state == IngestionState.TERMINAL_FAILED.value
    assert refreshed.lease_expires_at is None
    assert report.reclaimed == 1
    assert report.terminated == 1
    assert report.requeued == []


async def test_stale_queued_job_is_rescanned_and_requeued(db_session, stored_job_factory):
    """A `queued` job with no lease that was committed long ago (the process
    died, or the broker was unreachable, between the route's commit and its
    `send_task`/`.delay` call) must be picked back up by the sweeper and
    handed to the caller for re-enqueuing — without changing its state,
    since `claim` is idempotent against a duplicate message."""
    job = await stored_job_factory(state=IngestionState.QUEUED)
    await db_session.commit()
    stale_ts = datetime.now(timezone.utc) - timedelta(hours=2)
    await db_session.execute(
        text("UPDATE ingestion_jobs SET updated_at = :ts WHERE id = :id"),
        {"ts": stale_ts, "id": job.id},
    )
    await db_session.commit()

    report = await sweep(
        db_session,
        now=datetime.now(timezone.utc),
        max_attempts=3,
        queued_stale_seconds=_QUEUED_STALE_SECONDS,
    )
    await db_session.commit()

    refreshed = await db_session.get(IngestionJob, job.id)
    assert job.id in report.requeued
    assert refreshed.state == IngestionState.QUEUED.value


async def test_fresh_queued_job_is_not_rescanned(db_session, stored_job_factory):
    """A `queued` job just committed by a route (about to be enqueued
    normally) must NOT be treated as stranded — only jobs older than the
    lease duration are rescanned."""
    job = await stored_job_factory(state=IngestionState.QUEUED)
    await db_session.commit()

    report = await sweep(
        db_session,
        now=datetime.now(timezone.utc),
        max_attempts=3,
        queued_stale_seconds=_QUEUED_STALE_SECONDS,
    )
    await db_session.commit()

    assert job.id not in report.requeued
