from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models import SyncCursor, SyncSourceItem
from backend.app.services.sync.adapter import SourceItem


def _require_aware(moment: datetime) -> None:
    if moment.tzinfo is None or moment.tzinfo.utcoffset(moment) is None:
        raise ValueError("cursor must be timezone-aware")


def overlap_start(cursor_value: datetime, overlap_seconds: int) -> datetime:
    """Deliberately re-list a window before the cursor.

    Source timestamps tie and clocks skew, so a cursor used as an exclusive
    lower bound silently drops items. Re-seeing them is cheap because the
    ledger deduplicates; missing them is not recoverable.
    """
    _require_aware(cursor_value)
    return cursor_value - timedelta(seconds=overlap_seconds)


def next_cursor(current: datetime, processed: list[SourceItem]) -> datetime:
    """Advance to the newest processed item, never to `now`, never backwards."""
    _require_aware(current)
    if not processed:
        return current
    newest = max(item.updated_at for item in processed)
    return max(current, newest)


def identity_key(source: str, external_id: str, sha256: str) -> tuple[str, str, str]:
    for part in (source, external_id, sha256):
        if not part or not part.strip():
            raise ValueError("identity parts must be non-empty")
    return source, external_id, sha256


async def already_ingested(
    db: AsyncSession, *, source: str, external_id: str, sha256: str
) -> bool:
    """True when this exact content for this candidate was already ingested.

    The content hash is half the question (design §7), so this can only be
    asked once the bytes are in hand. Checked AFTER the download and BEFORE the
    job is created: it prevents the MinerU parse and the LLM extraction and
    scoring that a job would trigger, not the transfer itself.
    """
    identity_key(source, external_id, sha256)
    row = (
        await db.execute(
            select(SyncSourceItem.id).where(
                SyncSourceItem.source == source,
                SyncSourceItem.source_external_id == external_id,
                SyncSourceItem.content_sha256 == sha256,
                SyncSourceItem.outcome == "ingested",
            )
        )
    ).first()
    return row is not None


async def record_item(
    db: AsyncSession,
    *,
    source: str,
    external_id: str,
    sha256: str,
    outcome: str,
    now: datetime,
    job_id: int | None = None,
    error_code: str | None = None,
) -> SyncSourceItem:
    """Upsert the ledger row for one item, bumping attempts on a repeat.

    ONE statement, not select-then-insert-or-update. Beat publishes
    `sync.pull_dingtalk` every `DINGTALK_SYNC_INTERVAL_SECONDS` whether or not a
    worker is alive to consume it, so a worker outage queues messages that all
    dispatch at once on restart across a prefork pool: two runs reaching the
    same item is ordinary, not hypothetical. Read-then-write, both runs miss the
    SELECT, both INSERT, and one raises `IntegrityError` on
    `uq_sync_source_items_identity` — which lands in the runner's generic
    `except Exception` and calls `_record_failure` on the SAME triple in a new
    session, where the winner's committed row is now found and flipped to
    `outcome='failed', error_code='ingestion_failed'`. A candidate that was
    ingested correctly then reads as a failure in the operator report, and once
    `describe` is bound the sweeper re-downloads it. `ON CONFLICT DO UPDATE`
    keyed on the same three columns as the constraint makes the loser update the
    winner's row instead of raising, which is what the read-then-write branch
    was already trying to express.

    The semantics are unchanged and `backend/tests/integration/test_sync_ledger.py`
    pins them: an insert sets `attempts=1` and both timestamps; a conflict bumps
    `attempts`, advances `last_seen_at` only, leaves `first_seen_at` at the
    first sighting, and overwrites `ingestion_job_id` only when a non-None job
    id is supplied — the `coalesce` below, which is what `if job_id is not None`
    used to say.
    """
    statement = pg_insert(SyncSourceItem).values(
        source=source,
        source_external_id=external_id,
        content_sha256=sha256,
        ingestion_job_id=job_id,
        outcome=outcome,
        error_code=error_code,
        attempts=1,
        first_seen_at=now,
        last_seen_at=now,
    )
    upsert = statement.on_conflict_do_update(
        # The columns of `uq_sync_source_items_identity`, in its order.
        index_elements=["source", "source_external_id", "content_sha256"],
        set_={
            "outcome": statement.excluded.outcome,
            "error_code": statement.excluded.error_code,
            "attempts": SyncSourceItem.attempts + 1,
            "last_seen_at": statement.excluded.last_seen_at,
            "ingestion_job_id": func.coalesce(
                statement.excluded.ingestion_job_id, SyncSourceItem.ingestion_job_id
            ),
        },
    ).returning(SyncSourceItem.id)
    row_id = (await db.execute(upsert)).scalar_one()
    await db.flush()
    # Re-read rather than trust the identity map: on the conflict path the
    # session may already hold a stale copy of this row from an earlier call.
    return (
        await db.execute(
            select(SyncSourceItem)
            .where(SyncSourceItem.id == row_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


async def read_cursor(db: AsyncSession, source: str, *, default: datetime) -> datetime:
    row = await db.get(SyncCursor, source)
    if row is None:
        return default
    return datetime.fromisoformat(row.cursor_value)


async def write_cursor(
    db: AsyncSession, source: str, *, value: datetime, now: datetime
) -> None:
    _require_aware(value)
    row = await db.get(SyncCursor, source)
    stored = value.astimezone(timezone.utc).isoformat()
    if row is None:
        db.add(
            SyncCursor(
                source=source, cursor_value=stored, last_run_at=now, updated_at=now
            )
        )
    else:
        row.cursor_value = stored
        row.last_run_at = now
        row.updated_at = now
    await db.flush()
