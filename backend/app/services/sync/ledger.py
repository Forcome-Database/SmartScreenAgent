from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
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

    Checked BEFORE download, so a repeat costs one indexed lookup instead of a
    file transfer plus a paid parse and extraction.
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
    """Upsert the ledger row for one item, bumping attempts on a repeat."""
    existing = (
        await db.execute(
            select(SyncSourceItem).where(
                SyncSourceItem.source == source,
                SyncSourceItem.source_external_id == external_id,
                SyncSourceItem.content_sha256 == sha256,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        row = SyncSourceItem(
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
        db.add(row)
        await db.flush()
        return row
    existing.outcome = outcome
    existing.error_code = error_code
    existing.attempts += 1
    existing.last_seen_at = now
    if job_id is not None:
        existing.ingestion_job_id = job_id
    await db.flush()
    return existing


async def read_cursor(db: AsyncSession, source: str, *, default: datetime) -> datetime:
    row = await db.get(SyncCursor, source)
    if row is None:
        return default
    return datetime.fromisoformat(row.cursor_value)


async def write_cursor(
    db: AsyncSession, source: str, *, value: datetime, now: datetime
) -> None:
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
