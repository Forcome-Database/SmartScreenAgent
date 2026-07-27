from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.database import AsyncSessionLocal
from backend.app.models import IngestionJob, SyncCursor, SyncSourceItem
from backend.app.services.sync.ledger import (
    already_ingested,
    read_cursor,
    record_item,
    write_cursor,
)

pytestmark = pytest.mark.integration

NOW = datetime(2026, 7, 27, 5, 0, tzinfo=timezone.utc)
LATER = NOW + timedelta(minutes=10)


async def test_already_ingested_is_true_for_an_ingested_triple(db_session: AsyncSession) -> None:
    await record_item(
        db_session,
        source="dingtalk",
        external_id="cand-1",
        sha256="a" * 64,
        outcome="ingested",
        now=NOW,
    )

    assert (
        await already_ingested(
            db_session, source="dingtalk", external_id="cand-1", sha256="a" * 64
        )
        is True
    )


async def test_a_revised_resume_with_a_different_hash_is_not_ingested(
    db_session: AsyncSession,
) -> None:
    await record_item(
        db_session,
        source="dingtalk",
        external_id="cand-1",
        sha256="a" * 64,
        outcome="ingested",
        now=NOW,
    )

    # Same candidate, same source, but different content: must be ingested again.
    assert (
        await already_ingested(
            db_session, source="dingtalk", external_id="cand-1", sha256="b" * 64
        )
        is False
    )


async def test_a_failed_outcome_does_not_block_a_retry(db_session: AsyncSession) -> None:
    await record_item(
        db_session,
        source="dingtalk",
        external_id="cand-1",
        sha256="a" * 64,
        outcome="failed",
        now=NOW,
    )

    assert (
        await already_ingested(
            db_session, source="dingtalk", external_id="cand-1", sha256="a" * 64
        )
        is False
    )


async def test_second_record_item_on_the_same_triple_updates_rather_than_inserts(
    db_session: AsyncSession,
) -> None:
    first = await record_item(
        db_session,
        source="dingtalk",
        external_id="cand-1",
        sha256="a" * 64,
        outcome="failed",
        now=NOW,
    )
    await db_session.commit()

    await record_item(
        db_session,
        source="dingtalk",
        external_id="cand-1",
        sha256="a" * 64,
        outcome="ingested",
        now=LATER,
    )
    await db_session.commit()

    # Re-read from the database rather than trusting the in-memory object.
    row = await db_session.get(SyncSourceItem, first.id, populate_existing=True)
    assert row is not None
    assert row.attempts == 2
    assert row.outcome == "ingested"
    assert row.first_seen_at == NOW
    assert row.last_seen_at == LATER

    total = await db_session.scalar(
        select(func.count()).select_from(SyncSourceItem).where(
            SyncSourceItem.source == "dingtalk",
            SyncSourceItem.source_external_id == "cand-1",
            SyncSourceItem.content_sha256 == "a" * 64,
        )
    )
    assert total == 1


async def test_two_overlapping_runs_recording_the_same_item_do_not_collide() -> None:
    """A lost ledger race must not turn a successful ingest into a failure.

    Beat publishes `sync.pull_dingtalk` every interval whether or not a worker
    is alive, so an outage queues messages that all dispatch at once on restart
    across a prefork pool: two runs reaching the same candidate is ordinary.
    Select-then-insert, both miss the SELECT, both INSERT, and the loser raises
    `IntegrityError` on `uq_sync_source_items_identity` — which lands in
    `run_sync`'s generic `except Exception`, calls `_record_failure` on the SAME
    triple in a new session, and flips the winner's committed row to
    `outcome='failed', error_code='ingestion_failed'`. The candidate WAS
    ingested; the operator report says otherwise, and once `describe` is bound
    the sweeper re-downloads it.

    Two real sessions on one event loop interleave at every `await`, so both
    SELECTs are issued before either INSERT lands — the race, deterministically.
    """

    async def _record(when: datetime) -> None:
        async with AsyncSessionLocal() as session:
            await record_item(
                session,
                source="dingtalk",
                external_id="cand-race",
                sha256="c" * 64,
                outcome="ingested",
                now=when,
            )
            await session.commit()

    await asyncio.gather(_record(NOW), _record(LATER))

    async with AsyncSessionLocal() as session:
        rows = (
            (
                await session.execute(
                    select(SyncSourceItem).where(
                        SyncSourceItem.source_external_id == "cand-race"
                    )
                )
            )
            .scalars()
            .all()
        )

    # One row, and it still says what actually happened to the candidate.
    assert len(rows) == 1
    assert rows[0].outcome == "ingested"
    # The loser updated the winner's row rather than raising, so the second
    # sighting is counted exactly as a sequential repeat would have counted it.
    assert rows[0].attempts == 2


async def test_a_later_failure_does_not_erase_the_job_the_item_was_ingested_under(
    db_session: AsyncSession,
) -> None:
    """`ingestion_job_id` is overwritten only by a non-None job id.

    The ledger row is what points at the work already done. A later sighting
    that carries no job — every `_record_failure` — must leave that pointer
    alone, or the next run pays to download and parse the same bytes again.
    """
    digest = hashlib.sha256(uuid4().bytes).hexdigest()
    job = IngestionJob(
        state="queued",
        source="dingtalk",
        source_external_id="cand-keep",
        jd_code=None,
        raw_file_key=f"resumes/test/ledger-{digest[:16]}",
        raw_file_sha256=digest,
        raw_file_size_bytes=1234,
        raw_file_content_type="application/pdf",
        raw_file_original_name_cipher="cipher",
        attempts=0,
        actor="test",
    )
    db_session.add(job)
    await db_session.flush()

    await record_item(
        db_session,
        source="dingtalk",
        external_id="cand-keep",
        sha256="d" * 64,
        outcome="ingested",
        job_id=job.id,
        now=NOW,
    )
    await db_session.commit()

    row = await record_item(
        db_session,
        source="dingtalk",
        external_id="cand-keep",
        sha256="d" * 64,
        outcome="failed",
        error_code="item_unavailable",
        now=LATER,
    )
    await db_session.commit()

    assert row.ingestion_job_id == job.id
    assert row.outcome == "failed"
    assert row.error_code == "item_unavailable"
    assert row.attempts == 2
    assert row.first_seen_at == NOW


async def test_write_cursor_then_read_cursor_round_trips_a_tz_aware_instant(
    db_session: AsyncSession,
) -> None:
    await write_cursor(db_session, "dingtalk", value=NOW, now=NOW)
    await db_session.commit()

    read_back = await read_cursor(db_session, "dingtalk", default=NOW - timedelta(days=1))

    assert read_back == NOW
    assert read_back.tzinfo is not None


async def test_write_cursor_again_updates_the_existing_row_not_a_new_one(
    db_session: AsyncSession,
) -> None:
    await write_cursor(db_session, "dingtalk", value=NOW, now=NOW)
    await db_session.commit()

    await write_cursor(db_session, "dingtalk", value=LATER, now=LATER)
    await db_session.commit()

    read_back = await read_cursor(db_session, "dingtalk", default=NOW - timedelta(days=1))
    assert read_back == LATER

    total = await db_session.scalar(
        select(func.count()).select_from(SyncCursor).where(SyncCursor.source == "dingtalk")
    )
    assert total == 1
