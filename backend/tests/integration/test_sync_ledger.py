from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import SyncCursor, SyncSourceItem
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
