from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone
from hashlib import sha256 as _sha256

import pytest
from pypdf import PdfWriter
from sqlalchemy import func, select

from backend.app.database import AsyncSessionLocal
from backend.app.models import AuditLog, IngestionJob, SyncSourceItem
from backend.app.services.sync.adapter import (
    FetchedResume,
    ItemUnavailable,
    SourceCapabilityUnavailable,
    SourceItem,
)
from backend.app.services.sync.replay import replay_failed
from backend.app.services.sync.runner import UNKNOWN_SHA256

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

NOW = datetime.now(timezone.utc)
REPLAY_SOURCE = "stub-source"
OTHER_SOURCE = "other-source"
MAX_ATTEMPTS = 3


def _pdf(marker: str) -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.add_metadata({"/Title": marker})
    stream = io.BytesIO()
    writer.write(stream)
    return stream.getvalue()


def _item(external_id: str, *, minutes_ago: int = 90) -> SourceItem:
    return SourceItem(
        external_id=external_id,
        # Deliberately OLD: older than any plausible cursor. Replay must not
        # depend on the source timestamp having moved.
        updated_at=NOW - timedelta(minutes=minutes_ago),
        filename=f"{external_id}.pdf",
        content_type="application/pdf",
        jd_code=None,
    )


class ReplayAdapter:
    """Describes items by id and fetches them. Never lists.

    `list_changed` raises rather than returning empty: a sweeper that reached
    for the listing would then fail loudly instead of quietly looking correct
    while recovering nothing.
    """

    source_name = REPLAY_SOURCE

    def __init__(
        self,
        *,
        undescribable: bool = False,
        missing_ids: set[str] | None = None,
        fetch_fails: set[str] | None = None,
    ) -> None:
        self.undescribable = undescribable
        self.missing_ids = missing_ids or set()
        self.fetch_fails = fetch_fails or set()
        self.described: list[str] = []
        self.fetched: list[str] = []

    async def list_changed(self, since: datetime, limit: int) -> list[SourceItem]:
        raise AssertionError("replay must not re-list; it works from the ledger")

    async def describe(self, external_id: str) -> SourceItem:
        if self.undescribable:
            raise SourceCapabilityUnavailable("no single-item lookup")
        self.described.append(external_id)
        if external_id in self.missing_ids:
            raise ItemUnavailable("candidate is gone")
        return _item(external_id)

    async def fetch(self, item: SourceItem) -> FetchedResume:
        if item.external_id in self.fetch_fails:
            raise ItemUnavailable("attachment still missing")
        self.fetched.append(item.external_id)
        content = _pdf(item.external_id)
        return FetchedResume(
            content=content,
            sha256=_sha256(content).hexdigest(),
            filename=item.filename,
            content_type=item.content_type,
        )


class TrackingSessions:
    """Hands out real sessions but remembers them."""

    def __init__(self) -> None:
        self.opened: list = []

    def __call__(self):
        session = AsyncSessionLocal()
        self.opened.append(session)
        return session

    def any_in_transaction(self) -> bool:
        return any(session.in_transaction() for session in self.opened)


@pytest.fixture(autouse=True)
def enqueued(monkeypatch) -> list[int]:
    """No replay test may publish a real Celery message."""
    recorded: list[int] = []
    monkeypatch.setattr(
        "backend.app.services.sync.replay.enqueue_job",
        lambda job_id: recorded.append(job_id),
    )
    return recorded


async def _seed_failure(
    db_session,
    external_id: str,
    *,
    source: str = REPLAY_SOURCE,
    sha256: str = UNKNOWN_SHA256,
    error_code: str = "item_unavailable",
    attempts: int = 1,
) -> int:
    """A ledger row exactly as the runner leaves one behind."""
    row = SyncSourceItem(
        source=source,
        source_external_id=external_id,
        content_sha256=sha256,
        outcome="failed",
        error_code=error_code,
        attempts=attempts,
        first_seen_at=NOW - timedelta(hours=1),
        last_seen_at=NOW - timedelta(hours=1),
    )
    db_session.add(row)
    await db_session.commit()
    return row.id


async def _rows(db_session, external_id: str) -> list[SyncSourceItem]:
    await db_session.commit()
    result = await db_session.execute(
        select(SyncSourceItem)
        .where(SyncSourceItem.source_external_id == external_id)
        .execution_options(populate_existing=True)
    )
    return list(result.scalars())


async def test_a_failed_item_is_re_driven_by_external_id_without_any_listing(
    db_session, minio_storage, enqueued
):
    """The whole point of the sweeper.

    The cursor has moved past this item and the overlap will not reach back, so
    the ledger row is the only handle on it. `ReplayAdapter.list_changed`
    raises, so this passes only if the recovery came from `describe`.
    """
    await _seed_failure(db_session, "cand-1")
    adapter = ReplayAdapter()

    report = await replay_failed(
        AsyncSessionLocal, adapter, now=NOW, max_attempts=MAX_ATTEMPTS
    )

    assert adapter.described == ["cand-1"]
    assert report.replayed == 1
    assert report.failed == 0
    assert report.undescribable == 0
    jobs = (
        await db_session.execute(
            select(IngestionJob).where(IngestionJob.source_external_id == "cand-1")
        )
    ).scalars().all()
    assert len(jobs) == 1
    assert enqueued == [jobs[0].id]


async def test_replay_does_not_require_the_source_timestamp_to_have_moved(
    db_session, minio_storage
):
    # The common real recovery is "the recruiter attached the resume later",
    # which need not bump the candidate record's `updateTime`. The described
    # item here is 90 minutes old and no cursor is consulted at all.
    await _seed_failure(db_session, "cand-stale")
    adapter = ReplayAdapter()

    report = await replay_failed(
        AsyncSessionLocal, adapter, now=NOW, max_attempts=MAX_ATTEMPTS
    )

    assert report.replayed == 1
    assert adapter.fetched == ["cand-stale"]


async def test_a_successful_replay_is_not_selected_again_on_the_next_sweep(
    db_session, minio_storage
):
    """The superseded placeholder must stop matching.

    A fetch failure is recorded under `UNKNOWN_SHA256` because the bytes were
    never seen. A successful replay learns the real hash, so `record_item`
    writes a DIFFERENT identity row — leaving the placeholder `failed` and
    still inside the attempt bound. Without an explicit resolution it is
    selected on every sweep forever, re-downloading the same attachment until
    it exhausts.
    """
    await _seed_failure(db_session, "cand-2")
    adapter = ReplayAdapter()

    first = await replay_failed(
        AsyncSessionLocal, adapter, now=NOW, max_attempts=MAX_ATTEMPTS
    )
    second = await replay_failed(
        AsyncSessionLocal, adapter, now=NOW, max_attempts=MAX_ATTEMPTS
    )

    assert first.replayed == 1
    assert second.selected == 0
    assert second.replayed == 0
    assert adapter.described == ["cand-2"]
    rows = await _rows(db_session, "cand-2")
    # Exactly one row survives, under the real hash, and it is the ingested one.
    assert [(r.content_sha256 == UNKNOWN_SHA256, r.outcome) for r in rows] == [
        (False, "ingested")
    ]


async def test_an_item_already_ingested_elsewhere_resolves_without_a_second_job(
    db_session, minio_storage, enqueued
):
    # The runner re-lists a newest-in-window item on every run, so it may well
    # ingest the content itself before the sweeper reaches the placeholder.
    # The sweeper must then retire the placeholder, not create a rival job.
    await _seed_failure(db_session, "cand-3")
    adapter = ReplayAdapter()

    await replay_failed(AsyncSessionLocal, adapter, now=NOW, max_attempts=MAX_ATTEMPTS)
    await _seed_failure(db_session, "cand-3")
    report = await replay_failed(
        AsyncSessionLocal, adapter, now=NOW, max_attempts=MAX_ATTEMPTS
    )

    assert report.superseded == 1
    assert report.replayed == 0
    assert len(enqueued) == 1
    jobs = (
        await db_session.execute(
            select(func.count())
            .select_from(IngestionJob)
            .where(IngestionJob.source_external_id == "cand-3")
        )
    ).scalar_one()
    assert jobs == 1


async def test_an_always_failing_item_stops_being_attempted(db_session, minio_storage):
    """The bound, proven by exhaustion.

    Seeded at `attempts=1` exactly as the runner leaves it. Two more sweeps
    reach 3 = `SYNC_MAX_ITEM_ATTEMPTS`, after which the row no longer matches
    `attempts < max_attempts` and is never touched again.
    """
    await _seed_failure(db_session, "cand-doomed", attempts=1)
    adapter = ReplayAdapter(fetch_fails={"cand-doomed"})

    reports = [
        await replay_failed(
            AsyncSessionLocal, adapter, now=NOW, max_attempts=MAX_ATTEMPTS
        )
        for _ in range(5)
    ]

    assert [r.selected for r in reports] == [1, 1, 0, 0, 0]
    assert [r.failed for r in reports] == [1, 1, 0, 0, 0]
    # Five sweeps, two attempts: the source is left alone once the bound is hit.
    assert adapter.described == ["cand-doomed", "cand-doomed"]
    rows = await _rows(db_session, "cand-doomed")
    assert [(r.attempts, r.outcome) for r in rows] == [(3, "failed")]


async def test_exhaustion_keeps_the_reason_the_item_failed(db_session, minio_storage):
    # `error_code` is the diagnosis, and it is read exactly when someone
    # finally looks at a stuck row. Overwriting it with a terminality marker
    # would destroy the one field that says why.
    await _seed_failure(db_session, "cand-doomed", error_code="unsupported_attachment")
    adapter = ReplayAdapter(fetch_fails={"cand-doomed"})

    for _ in range(4):
        await replay_failed(
            AsyncSessionLocal, adapter, now=NOW, max_attempts=MAX_ATTEMPTS
        )

    rows = await _rows(db_session, "cand-doomed")
    assert rows[0].attempts == MAX_ATTEMPTS
    assert rows[0].error_code == "item_unavailable"
    assert rows[0].outcome == "failed"


async def test_an_adapter_that_cannot_describe_spends_no_attempt(
    db_session, minio_storage
):
    """An unbuilt capability must not consume the rescue budget.

    This is the DingTalk case today. If a refusal counted as an attempt, three
    sweeps would push every recoverable row past the bound and destroy it —
    data loss caused by a missing feature rather than by any failure.
    """
    await _seed_failure(db_session, "cand-4")
    adapter = ReplayAdapter(undescribable=True)

    first = await replay_failed(
        AsyncSessionLocal, adapter, now=NOW, max_attempts=MAX_ATTEMPTS
    )
    for _ in range(4):
        await replay_failed(
            AsyncSessionLocal, adapter, now=NOW, max_attempts=MAX_ATTEMPTS
        )

    assert first.selected == 1
    assert first.undescribable == 1
    assert first.failed == 0
    assert first.replayed == 0
    rows = await _rows(db_session, "cand-4")
    # Untouched: still one attempt spent, still recoverable when the binding lands.
    assert rows[0].attempts == 1
    assert rows[0].error_code == "item_unavailable"


async def test_an_item_the_source_no_longer_has_spends_an_attempt(
    db_session, minio_storage
):
    # `ItemUnavailable` from `describe` is a real per-item failure — the source
    # has the lookup and answered "not here" — so it is bounded like any other.
    await _seed_failure(db_session, "cand-gone")
    adapter = ReplayAdapter(missing_ids={"cand-gone"})

    report = await replay_failed(
        AsyncSessionLocal, adapter, now=NOW, max_attempts=MAX_ATTEMPTS
    )

    assert report.failed == 1
    assert report.undescribable == 0
    rows = await _rows(db_session, "cand-gone")
    assert rows[0].attempts == 2


async def test_only_this_sources_rows_are_replayed(db_session, minio_storage):
    # One adapter must never re-drive another source's ledger rows; its
    # `describe` would be asked for an id that means nothing to it.
    await _seed_failure(db_session, "cand-mine")
    await _seed_failure(db_session, "cand-theirs", source=OTHER_SOURCE)
    adapter = ReplayAdapter()

    report = await replay_failed(
        AsyncSessionLocal, adapter, now=NOW, max_attempts=MAX_ATTEMPTS
    )

    assert report.selected == 1
    assert adapter.described == ["cand-mine"]


async def test_an_ingested_row_is_never_replayed(db_session, minio_storage):
    row = SyncSourceItem(
        source=REPLAY_SOURCE,
        source_external_id="cand-done",
        content_sha256="a" * 64,
        outcome="ingested",
        attempts=1,
        first_seen_at=NOW,
        last_seen_at=NOW,
    )
    db_session.add(row)
    await db_session.commit()
    adapter = ReplayAdapter()

    report = await replay_failed(
        AsyncSessionLocal, adapter, now=NOW, max_attempts=MAX_ATTEMPTS
    )

    assert report.selected == 0
    assert adapter.described == []


async def test_no_business_transaction_is_held_across_the_replay_fetch(
    db_session, minio_storage
):
    await _seed_failure(db_session, "cand-5")
    sessions = TrackingSessions()

    class Watching(ReplayAdapter):
        async def fetch(self, item):
            assert not sessions.any_in_transaction()
            return await super().fetch(item)

        async def describe(self, external_id):
            assert not sessions.any_in_transaction()
            return await super().describe(external_id)

    report = await replay_failed(
        sessions, Watching(), now=NOW, max_attempts=MAX_ATTEMPTS
    )

    assert report.replayed == 1


async def test_the_replay_audit_carries_counts_not_candidate_data(
    db_session, minio_storage
):
    await _seed_failure(db_session, "cand-6")
    await _seed_failure(db_session, "cand-doomed")
    adapter = ReplayAdapter(fetch_fails={"cand-doomed"})

    await replay_failed(AsyncSessionLocal, adapter, now=NOW, max_attempts=MAX_ATTEMPTS)

    audit = (
        await db_session.execute(
            select(AuditLog).where(AuditLog.event_type == "resume_sync_replayed")
        )
    ).scalars().one()
    assert audit.payload["source"] == REPLAY_SOURCE
    assert audit.payload["selected"] == 2
    assert audit.payload["replayed"] == 1
    assert audit.payload["failed"] == 1
    assert audit.payload["undescribable"] == 0
    serialized = str(audit.payload)
    for forbidden in ("cand-6", "cand-doomed", ".pdf", "resumes/"):
        assert forbidden not in serialized


async def test_an_undescribable_sweep_is_visible_in_the_audit(
    db_session, minio_storage
):
    # `replayed: 0, failed: 0` alone reads as "the queue is clean". It is not —
    # the queue is untouched, and the audit has to say which.
    await _seed_failure(db_session, "cand-7")
    adapter = ReplayAdapter(undescribable=True)

    await replay_failed(AsyncSessionLocal, adapter, now=NOW, max_attempts=MAX_ATTEMPTS)

    audit = (
        await db_session.execute(
            select(AuditLog).where(AuditLog.event_type == "resume_sync_replayed")
        )
    ).scalars().one()
    assert audit.payload["undescribable"] == 1
    assert audit.payload["replayed"] == 0
    assert audit.payload["failed"] == 0
