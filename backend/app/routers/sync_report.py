from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import get_settings
from backend.app.database import get_db
from backend.app.deps import require_roles
from backend.app.models import SyncCursor, SyncSourceItem, User
from backend.app.schemas.sync_report import SyncReportResponse, SyncSourceReport

router = APIRouter(prefix="/api/v1", tags=["sync"])
READ_ROLES = ("hr", "hr_lead", "admin")


@router.get("/sync/report", response_model=SyncReportResponse)
async def sync_report(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_roles(*READ_ROLES)),
) -> SyncReportResponse:
    """Where each sync source stands, and how much of it needs a human.

    Not paginated: one row per source that has ever run, and a deployment has a
    handful of sources at most.

    Only `ingested` and `failed` are counted. The `outcome` CHECK constraint
    also admits `skipped_duplicate`, but nothing writes it — the runner detects
    a duplicate through `already_ingested` and never records a row for it — so a
    `skipped_total` here would be a permanent zero that reads like evidence
    deduplication is not happening.
    """
    settings = get_settings()
    max_attempts = settings.SYNC_MAX_ITEM_ATTEMPTS

    counts = (
        await db.execute(
            select(
                SyncSourceItem.source,
                func.count().filter(SyncSourceItem.outcome == "ingested").label("ingested"),
                func.count()
                .filter(
                    SyncSourceItem.outcome == "failed",
                    SyncSourceItem.attempts < max_attempts,
                )
                .label("retrying"),
                # `>=`, not `==`: the bound is configuration and can be lowered
                # under rows that already passed the old one. Those rows are
                # terminal too, and an equality test would silently drop them.
                func.count()
                .filter(
                    SyncSourceItem.outcome == "failed",
                    SyncSourceItem.attempts >= max_attempts,
                )
                .label("terminal"),
            ).group_by(SyncSourceItem.source)
        )
    ).all()

    cursors = (
        await db.execute(select(SyncCursor.source, SyncCursor.cursor_value, SyncCursor.last_run_at))
    ).all()

    by_source = {row.source: row for row in counts}
    cursor_by_source = {row.source: row for row in cursors}

    items: list[SyncSourceReport] = []
    # The union of both tables, not either one alone: a run that took nothing in
    # leaves a cursor and no ledger rows, and a run that aborted before writing
    # its cursor leaves ledger rows and no cursor.
    for source in sorted(set(by_source) | set(cursor_by_source)):
        count = by_source.get(source)
        cursor = cursor_by_source.get(source)
        items.append(
            SyncSourceReport(
                source=source,
                cursor_value=cursor.cursor_value if cursor is not None else None,
                last_run_at=cursor.last_run_at if cursor is not None else None,
                ingested_total=count.ingested if count is not None else 0,
                failed_retrying_total=count.retrying if count is not None else 0,
                failed_terminal_total=count.terminal if count is not None else 0,
            )
        )
    return SyncReportResponse(items=items, max_item_attempts=max_attempts)
