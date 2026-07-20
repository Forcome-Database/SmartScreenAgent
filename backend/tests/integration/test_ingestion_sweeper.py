from datetime import datetime, timedelta, timezone

import pytest

from backend.app.models import IngestionJob
from backend.app.services.ingestion.states import IngestionState
from backend.app.services.ingestion.sweeper import sweep

pytestmark = pytest.mark.integration


async def test_expired_lease_is_reclaimed_and_requeued(db_session, stored_job_factory):
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    job = await stored_job_factory(state=IngestionState.PARSING, lease_expires_at=past, attempts=1)
    await db_session.commit()
    report = await sweep(db_session, now=datetime.now(timezone.utc), max_attempts=3)
    await db_session.commit()
    refreshed = await db_session.get(IngestionJob, job.id)
    assert job.id in report.requeued
    assert refreshed.state == IngestionState.QUEUED.value


async def test_retryable_at_cap_terminates(db_session, stored_job_factory):
    job = await stored_job_factory(state=IngestionState.RETRYABLE_FAILED, attempts=3)
    await db_session.commit()
    report = await sweep(db_session, now=datetime.now(timezone.utc), max_attempts=3)
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
    report = await sweep(db_session, now=datetime.now(timezone.utc), max_attempts=3)
    await db_session.commit()
    refreshed = await db_session.get(IngestionJob, job.id)
    assert refreshed.state == IngestionState.TERMINAL_FAILED.value
    assert refreshed.lease_expires_at is None
    assert report.reclaimed == 1
    assert report.terminated == 1
    assert report.requeued == []
