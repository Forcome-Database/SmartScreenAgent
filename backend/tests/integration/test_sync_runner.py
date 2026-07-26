from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone
from hashlib import sha256 as _sha256

import pytest
from pypdf import PdfWriter
from sqlalchemy import func, select

from backend.app.database import AsyncSessionLocal
from backend.app.models import AuditLog, IngestionJob, SyncCursor, SyncSourceItem
from backend.app.services.storage.resume_storage import ResumeStorageService
from backend.app.services.sync.adapter import (
    FetchedResume,
    ItemUnavailable,
    SourceItem,
    SourceUnavailable,
)
from backend.app.services.sync.runner import run_sync

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

NOW = datetime.now(timezone.utc)


def _pdf(marker: str) -> bytes:
    """A real PDF, because WP1 validation opens it with pypdf.

    The marker only rides along as document metadata, which is enough to give
    every candidate distinct bytes and therefore a distinct sha256.
    """
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.add_metadata({"/Title": marker})
    stream = io.BytesIO()
    writer.write(stream)
    return stream.getvalue()


class StubAdapter:
    source_name = "dingtalk"

    def __init__(self, items: list[SourceItem], *, fail_ids: set[str] | None = None):
        self.items = items
        self.fail_ids = fail_ids or set()
        self.fetched: list[str] = []
        self.listed = 0

    async def list_changed(self, since: datetime, limit: int) -> list[SourceItem]:
        self.listed += 1
        return [i for i in self.items if i.updated_at >= since][:limit]

    async def fetch(self, item: SourceItem) -> FetchedResume:
        if item.external_id in self.fail_ids:
            raise ItemUnavailable("attachment missing")
        self.fetched.append(item.external_id)
        content = _pdf(item.external_id)
        return FetchedResume(
            content=content,
            sha256=_sha256(content).hexdigest(),
            filename=item.filename,
            content_type=item.content_type,
        )


class BrokenAdapter:
    source_name = "dingtalk"

    async def list_changed(self, since: datetime, limit: int) -> list[SourceItem]:
        raise SourceUnavailable("permission revoked")

    async def fetch(self, item: SourceItem) -> FetchedResume:
        raise AssertionError("must not be reached")


class TrackingSessions:
    """Hands out real sessions but remembers them.

    The runner's own sessions are the ones that matter for "no business
    transaction across a network call" — asserting on the test's `db_session`
    would prove nothing, because the runner never touches it.
    """

    def __init__(self) -> None:
        self.opened: list = []

    def __call__(self):
        session = AsyncSessionLocal()
        self.opened.append(session)
        return session

    def any_in_transaction(self) -> bool:
        return any(session.in_transaction() for session in self.opened)


def _item(external_id: str, *, minutes_ago: int = 10, filename: str | None = None) -> SourceItem:
    return SourceItem(
        external_id=external_id,
        updated_at=NOW - timedelta(minutes=minutes_ago),
        filename=filename or f"{external_id}.pdf",
        content_type="application/pdf",
        jd_code=None,
    )


@pytest.fixture(autouse=True)
def enqueued(monkeypatch) -> list[int]:
    """No runner test may publish a real Celery message.

    The seam is the same module-level `enqueue_job` indirection the upload
    route uses, so patching it here exercises everything up to `.delay()`.
    """
    recorded: list[int] = []
    monkeypatch.setattr(
        "backend.app.services.sync.runner.enqueue_job",
        lambda job_id: recorded.append(job_id),
    )
    return recorded


async def test_a_repeat_run_ingests_nothing_and_fetches_nothing(db_session, minio_storage):
    adapter = StubAdapter([_item("c1"), _item("c2")])

    first = await run_sync(
        AsyncSessionLocal, adapter, now=NOW, overlap_seconds=300, max_items=200
    )
    assert (first.ingested, first.skipped) == (2, 0)
    assert adapter.fetched == ["c1", "c2"]

    second = await run_sync(
        AsyncSessionLocal, adapter, now=NOW, overlap_seconds=300, max_items=200
    )

    assert (second.ingested, second.skipped) == (0, 2)
    # The saving is money, not just rows: no second download happened.
    assert adapter.fetched == ["c1", "c2"]
    jobs = (
        await db_session.execute(select(func.count()).select_from(IngestionJob))
    ).scalar_one()
    assert jobs == 2


async def test_a_recently_updated_item_stops_being_downloaded(db_session, minio_storage):
    """An item first seen inside the overlap window must converge on skipping.

    Its source timestamp sits too close to the sighting for the pre-download
    guard to trust it, so it is re-downloaded once — and then never again.
    """
    adapter = StubAdapter([_item("c1", minutes_ago=1)])

    await run_sync(AsyncSessionLocal, adapter, now=NOW, overlap_seconds=300, max_items=200)
    assert adapter.fetched == ["c1"]

    # Half an hour on, it is still the newest thing the source has to offer.
    await run_sync(
        AsyncSessionLocal,
        adapter,
        now=NOW + timedelta(minutes=30),
        overlap_seconds=300,
        max_items=200,
    )
    assert adapter.fetched == ["c1", "c1"]

    await run_sync(
        AsyncSessionLocal,
        adapter,
        now=NOW + timedelta(minutes=60),
        overlap_seconds=300,
        max_items=200,
    )
    assert adapter.fetched == ["c1", "c1"]
    jobs = (
        await db_session.execute(select(func.count()).select_from(IngestionJob))
    ).scalar_one()
    assert jobs == 1


async def test_a_changed_resume_is_downloaded_again(db_session, minio_storage):
    """The pre-download guard must not permanently ignore a revised resume."""

    class Revising(StubAdapter):
        marker = "v1"

        async def fetch(self, item: SourceItem) -> FetchedResume:
            self.fetched.append(item.external_id)
            content = _pdf(self.marker)
            return FetchedResume(
                content=content,
                sha256=_sha256(content).hexdigest(),
                filename=item.filename,
                content_type=item.content_type,
            )

    adapter = Revising([_item("c1")])
    await run_sync(AsyncSessionLocal, adapter, now=NOW, overlap_seconds=300, max_items=200)

    later = NOW + timedelta(hours=1)
    adapter.marker = "v2"
    adapter.items = [
        SourceItem(
            external_id="c1",
            updated_at=later,
            filename="c1-v2.pdf",
            content_type="application/pdf",
            jd_code=None,
        )
    ]

    report = await run_sync(
        AsyncSessionLocal,
        adapter,
        now=later + timedelta(minutes=1),
        overlap_seconds=300,
        max_items=200,
    )

    assert report.ingested == 1
    assert adapter.fetched == ["c1", "c1"]
    rows = (
        (
            await db_session.execute(
                select(SyncSourceItem).where(SyncSourceItem.source_external_id == "c1")
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 2
    assert {row.outcome for row in rows} == {"ingested"}


async def test_one_bad_item_does_not_abort_the_run(db_session, minio_storage):
    adapter = StubAdapter([_item("good1"), _item("bad"), _item("good2")], fail_ids={"bad"})

    report = await run_sync(
        AsyncSessionLocal, adapter, now=NOW, overlap_seconds=300, max_items=200
    )

    assert (report.ingested, report.failed) == (2, 1)
    failed = (
        await db_session.execute(
            select(SyncSourceItem).where(SyncSourceItem.outcome == "failed")
        )
    ).scalar_one()
    assert failed.source_external_id == "bad"
    assert failed.error_code == "item_unavailable"


async def test_an_unsupported_attachment_is_one_failed_item(db_session, minio_storage):
    """A recruitment attachment is not guaranteed to be a format WP1 accepts."""

    class Spreadsheets(StubAdapter):
        async def fetch(self, item: SourceItem) -> FetchedResume:
            self.fetched.append(item.external_id)
            content = b"external_id,score\n"
            return FetchedResume(
                content=content,
                sha256=_sha256(content).hexdigest(),
                filename="notes.csv",
                content_type="text/csv",
            )

    adapter = Spreadsheets([_item("c1")])

    report = await run_sync(
        AsyncSessionLocal, adapter, now=NOW, overlap_seconds=300, max_items=200
    )

    assert (report.ingested, report.failed) == (0, 1)
    failed = (await db_session.execute(select(SyncSourceItem))).scalar_one()
    assert failed.error_code == "unsupported_attachment"
    assert failed.outcome == "failed"
    jobs = (
        await db_session.execute(select(func.count()).select_from(IngestionJob))
    ).scalar_one()
    assert jobs == 0


async def test_a_synced_attachment_is_malware_scanned(db_session, minio_storage, monkeypatch):
    """A downloaded attachment is less trusted than an HR upload, not more."""

    class Rejecting:
        def __init__(self) -> None:
            self.scanned: list[str] = []

        async def scan(self, artifact) -> None:
            self.scanned.append(artifact.sha256)
            raise RuntimeError("infected")

    scanner = Rejecting()
    monkeypatch.setattr(
        "backend.app.services.sync.runner.get_malware_scanner", lambda mode: scanner
    )
    adapter = StubAdapter([_item("c1")])

    report = await run_sync(
        AsyncSessionLocal, adapter, now=NOW, overlap_seconds=300, max_items=200
    )

    assert scanner.scanned, "the sync path must scan what it downloads"
    assert (report.ingested, report.failed) == (0, 1)
    # Rejected before the object was stored, so nothing was left behind.
    assert minio_storage.list_object_keys(prefix="resumes/") == []


async def test_a_listing_failure_leaves_the_cursor_untouched(db_session, minio_storage):
    with pytest.raises(SourceUnavailable):
        await run_sync(
            AsyncSessionLocal, BrokenAdapter(), now=NOW, overlap_seconds=300, max_items=200
        )

    items = (
        await db_session.execute(select(func.count()).select_from(SyncSourceItem))
    ).scalar_one()
    assert items == 0
    cursors = (
        await db_session.execute(select(func.count()).select_from(SyncCursor))
    ).scalar_one()
    assert cursors == 0
    audit = (
        await db_session.execute(
            select(AuditLog).where(AuditLog.event_type == "resume_sync_failed")
        )
    ).scalar_one()
    assert audit.payload["error_code"] == "source_unavailable"
    # The provider's own words may name a person; only our codes may be stored.
    assert "permission revoked" not in str(audit.payload)


async def test_the_per_run_cap_is_reported_not_silent(db_session, minio_storage):
    adapter = StubAdapter([_item(f"c{i}") for i in range(5)])

    report = await run_sync(
        AsyncSessionLocal, adapter, now=NOW, overlap_seconds=300, max_items=2
    )

    assert report.ingested == 2
    # Silent truncation would read as "sync finished".
    assert report.dropped_by_cap >= 1
    assert report.listed == 2
    audit = (
        await db_session.execute(
            select(AuditLog).where(AuditLog.event_type == "resume_sync_completed")
        )
    ).scalar_one()
    assert audit.payload["dropped_by_cap"] == report.dropped_by_cap


async def test_no_business_transaction_is_held_across_the_fetch(db_session, minio_storage):
    sessions = TrackingSessions()

    class Asserting(StubAdapter):
        async def fetch(self, item):
            assert not sessions.any_in_transaction()
            return await super().fetch(item)

    adapter = Asserting([_item("c1")])

    await run_sync(sessions, adapter, now=NOW, overlap_seconds=300, max_items=200)

    assert adapter.fetched == ["c1"]


async def test_no_business_transaction_is_held_across_the_object_store(
    db_session, minio_storage, monkeypatch
):
    sessions = TrackingSessions()
    real_store = ResumeStorageService.store
    observed: list[bool] = []

    async def guarded_store(self, artifact):
        observed.append(sessions.any_in_transaction())
        return await real_store(self, artifact)

    monkeypatch.setattr(ResumeStorageService, "store", guarded_store)

    await run_sync(
        sessions, StubAdapter([_item("c1")]), now=NOW, overlap_seconds=300, max_items=200
    )

    assert observed == [False]


async def test_ingested_candidates_carry_their_source(db_session, minio_storage):
    adapter = StubAdapter([_item("cand-42")])

    await run_sync(AsyncSessionLocal, adapter, now=NOW, overlap_seconds=300, max_items=200)

    job = (await db_session.execute(select(IngestionJob))).scalar_one()
    assert job.source == "dingtalk"
    assert job.source_external_id == "cand-42"


async def test_only_newly_created_jobs_are_enqueued(db_session, minio_storage, enqueued):
    adapter = StubAdapter([_item("c1")])

    await run_sync(AsyncSessionLocal, adapter, now=NOW, overlap_seconds=300, max_items=200)

    job = (await db_session.execute(select(IngestionJob))).scalar_one()
    assert enqueued == [job.id]

    await run_sync(AsyncSessionLocal, adapter, now=NOW, overlap_seconds=300, max_items=200)

    # A repeat is deduplicated before it can queue a second parse.
    assert enqueued == [job.id]


async def test_the_completed_audit_carries_counts_not_candidate_data(db_session, minio_storage):
    adapter = StubAdapter([_item("c1", filename="Zhang Wei resume.pdf")])

    report = await run_sync(
        AsyncSessionLocal, adapter, now=NOW, overlap_seconds=300, max_items=200
    )

    audit = (
        await db_session.execute(
            select(AuditLog).where(AuditLog.event_type == "resume_sync_completed")
        )
    ).scalar_one()
    assert set(audit.payload) == {
        "source",
        "cursor_from",
        "cursor_to",
        "listed",
        "ingested",
        "skipped",
        "failed",
        "dropped_by_cap",
    }
    assert audit.payload["ingested"] == 1
    assert audit.payload["cursor_to"] == report.cursor_to.isoformat()
    # The filename is candidate-supplied and may be a name.
    assert "Zhang" not in str(audit.payload)
