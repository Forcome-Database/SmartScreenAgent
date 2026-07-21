import hashlib
from uuid import uuid4

import pytest

from backend.app.services.ingestion.jobs import IngestionJobService
from backend.app.services.ingestion.states import IngestionState, InvalidTransitionError
from backend.app.tasks.ingest import RawFileReference

pytestmark = pytest.mark.integration


def _ref(sha: str) -> RawFileReference:
    return RawFileReference(
        object_key=f"resumes/2026/07/{sha}",
        sha256=sha,
        size_bytes=1234,
        content_type="application/pdf",
        original_name_cipher="cipher",
    )


def _unique_sha() -> str:
    """A fresh 64-hex sha256-shaped value, unique per call.

    ingestion_jobs is not truncated between tests, so literal fixed shas
    (like the "a" * 64 used elsewhere in this file) risk collisions with
    stray rows left behind by earlier or interrupted test runs.
    """
    return hashlib.sha256(uuid4().bytes).hexdigest()


async def test_create_then_reuse_by_sha(db_session):
    svc = IngestionJobService(db_session)
    job1, created1 = await svc.create_or_reuse(
        raw_file=_ref("a" * 64), source="upload", source_external_id=None,
        jd_code=None, actor="user:1",
    )
    await db_session.commit()
    job2, created2 = await svc.create_or_reuse(
        raw_file=_ref("a" * 64), source="upload", source_external_id=None,
        jd_code=None, actor="user:1",
    )
    assert created1 is True and created2 is False
    assert job2.id == job1.id


async def test_claim_sets_lease_and_increments_attempts(db_session):
    svc = IngestionJobService(db_session)
    job, _ = await svc.create_or_reuse(
        raw_file=_ref("b" * 64), source="upload", source_external_id=None,
        jd_code="FOREIGN_TRADE", actor="user:1",
    )
    await db_session.commit()
    claimed = await svc.claim(job.id, lease_seconds=900)
    assert claimed is not None
    assert claimed.state == IngestionState.PARSING.value
    assert claimed.attempts == 1
    assert claimed.lease_expires_at is not None


async def test_create_or_reuse_does_not_reuse_terminal_job(db_session):
    sha = _unique_sha()
    svc = IngestionJobService(db_session)
    job1, created1 = await svc.create_or_reuse(
        raw_file=_ref(sha), source="upload", source_external_id=None,
        jd_code=None, actor="user:1",
    )
    assert created1 is True
    job1.state = IngestionState.COMPLETED.value
    await db_session.commit()

    job2, created2 = await svc.create_or_reuse(
        raw_file=_ref(sha), source="upload", source_external_id=None,
        jd_code=None, actor="user:1",
    )
    assert created2 is True
    assert job2.id != job1.id


async def test_claim_returns_none_when_job_not_queued(db_session):
    svc = IngestionJobService(db_session)
    job, _ = await svc.create_or_reuse(
        raw_file=_ref(_unique_sha()), source="upload", source_external_id=None,
        jd_code=None, actor="user:1",
    )
    await db_session.commit()
    first = await svc.claim(job.id, lease_seconds=900)
    assert first is not None
    assert first.state == IngestionState.PARSING.value
    await db_session.commit()

    second = await svc.claim(job.id, lease_seconds=900)
    assert second is None


async def test_claim_returns_none_when_job_missing(db_session):
    svc = IngestionJobService(db_session)
    missing = await svc.claim(2_147_483_647, lease_seconds=900)
    assert missing is None


async def test_transition_raises_on_invalid_transition(db_session):
    svc = IngestionJobService(db_session)
    job, _ = await svc.create_or_reuse(
        raw_file=_ref(_unique_sha()), source="upload", source_external_id=None,
        jd_code=None, actor="user:1",
    )
    await db_session.commit()

    with pytest.raises(InvalidTransitionError):
        # queued -> completed skips the required parsing/extracting/scoring steps.
        await svc.transition(job, IngestionState.COMPLETED)


async def test_transition_lease_clearing_semantics(db_session):
    svc = IngestionJobService(db_session)
    job, _ = await svc.create_or_reuse(
        raw_file=_ref(_unique_sha()), source="upload", source_external_id=None,
        jd_code=None, actor="user:1",
    )
    await db_session.commit()
    claimed = await svc.claim(job.id, lease_seconds=900)
    await db_session.commit()
    assert claimed.lease_expires_at is not None

    # extracting and scoring are both processing states: lease is preserved.
    await svc.transition(claimed, IngestionState.EXTRACTING)
    assert claimed.lease_expires_at is not None

    await svc.transition(claimed, IngestionState.SCORING)
    assert claimed.lease_expires_at is not None

    # completed is not a processing state: lease is cleared.
    await svc.transition(claimed, IngestionState.COMPLETED)
    assert claimed.lease_expires_at is None


async def test_batch_counts_aggregates_by_state(db_session):
    svc = IngestionJobService(db_session)
    batch_id = uuid4()
    job1, _ = await svc.create_or_reuse(
        raw_file=_ref(_unique_sha()), source="upload", source_external_id=None,
        jd_code=None, actor="user:1", batch_id=batch_id,
    )
    job2, _ = await svc.create_or_reuse(
        raw_file=_ref(_unique_sha()), source="upload", source_external_id=None,
        jd_code=None, actor="user:1", batch_id=batch_id,
    )
    job3, _ = await svc.create_or_reuse(
        raw_file=_ref(_unique_sha()), source="upload", source_external_id=None,
        jd_code=None, actor="user:1", batch_id=batch_id,
    )
    await db_session.commit()
    await svc.claim(job2.id, lease_seconds=900)
    await svc.claim(job3.id, lease_seconds=900)
    await db_session.commit()

    counts = await svc.batch_counts(batch_id)
    assert counts == {
        IngestionState.QUEUED.value: 1,
        IngestionState.PARSING.value: 2,
    }
    assert job1.state == IngestionState.QUEUED.value
