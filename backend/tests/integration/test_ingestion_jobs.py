import pytest

from backend.app.services.ingestion.jobs import IngestionJobService
from backend.app.services.ingestion.states import IngestionState
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
