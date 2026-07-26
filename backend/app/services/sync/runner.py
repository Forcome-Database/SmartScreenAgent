from __future__ import annotations

import io
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

import structlog
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile

from backend.app.config import get_settings
from backend.app.models import AuditLog
from backend.app.services.ingestion.intake import enqueue_job, ingest_upload
from backend.app.services.ingestion.jobs import IngestionJobService
from backend.app.services.storage import ResumeStorageService
from backend.app.services.sync.adapter import (
    ItemUnavailable,
    ResumeSourceAdapter,
    SourceItem,
    SourceUnavailable,
)
from backend.app.services.sync.ledger import (
    already_ingested,
    next_cursor,
    overlap_start,
    read_cursor,
    record_item,
    write_cursor,
)
from backend.app.services.upload import UploadValidationError, UploadValidator, get_malware_scanner

logger = structlog.get_logger(__name__)

# How far back a first-ever run reaches when no cursor exists yet.
FIRST_RUN_LOOKBACK = timedelta(days=1)

# The ledger identity needs a content hash, but an item that could not be
# downloaded has none. This stands in for it so the failure still gets a row
# the replay sweeper can find and count attempts on.
UNKNOWN_SHA256 = "0" * 64

SessionFactory = Callable[[], AsyncSession]


@dataclass(frozen=True)
class SyncReport:
    """What one run took in.

    `truncated` is the answer to "did the cap bite?" — the only question about
    the cap that can be answered exactly. The run asks the source for one item
    more than it will process, so a truncated run can never be mistaken for a
    finished one.

    `dropped_at_least` is a floor on how many items were left behind, never a
    census: how many more the source still holds cannot be known without paging
    all of it, which is precisely what the cap exists to prevent. With the
    `+1` probe it is 1 on any truncated run, whether one resume or ten thousand
    are waiting. Alert on `truncated`, not on this magnitude.
    """

    listed: int
    ingested: int
    skipped: int
    failed: int
    dropped_at_least: int
    truncated: bool
    cursor_from: datetime
    cursor_to: datetime


async def run_sync(
    session_factory: SessionFactory,
    adapter: ResumeSourceAdapter,
    *,
    now: datetime,
    overlap_seconds: int,
    max_items: int,
) -> SyncReport:
    """Pull one batch of changed resumes into the WP3 pipeline.

    Source-agnostic by construction: it knows an adapter, never a provider.
    Every database write is its own short transaction; the adapter's HTTP call
    and the MinIO upload happen with no transaction open. A stalled provider
    must never hold connections that manual upload also needs.
    """
    source = adapter.source_name
    actor = f"system:sync:{source}"
    settings = get_settings()
    # Built once, before anything is listed: a misconfigured scan mode is one
    # loud configuration error, not N `ingestion_failed` ledger rows under a
    # report that claims the run completed.
    scanner = get_malware_scanner(settings.MALWARE_SCAN_MODE)

    async with session_factory() as session:
        cursor = await read_cursor(session, source, default=now - FIRST_RUN_LOOKBACK)
        await session.commit()

    since = overlap_start(cursor, overlap_seconds)
    try:
        # One more than the cap is requested on purpose: the surplus item is
        # the only evidence available that the run was truncated rather than
        # finished, and §9.1 forbids truncating silently.
        offered = await adapter.list_changed(since, max_items + 1)
    except SourceUnavailable:
        # The cursor is deliberately left alone so the next run retries the
        # same window. Retrying in-task would only spin on a revoked token.
        await _audit(
            session_factory,
            event_type="resume_sync_failed",
            actor=actor,
            payload={
                "source": source,
                "cursor_from": cursor.isoformat(),
                "error_code": "source_unavailable",
            },
        )
        logger.error("resume_sync_failed", source=source, error_code="source_unavailable")
        raise

    items = offered[:max_items]
    dropped_at_least = len(offered) - len(items)
    truncated = dropped_at_least > 0
    if truncated:
        logger.warning(
            "resume_sync_capped",
            source=source,
            listed=len(items),
            truncated=True,
            dropped_at_least=dropped_at_least,
            max_items=max_items,
        )

    processed: list[SourceItem] = []
    ingested = skipped = failed = 0

    for item in items:
        try:
            fetched = await adapter.fetch(item)
        except ItemUnavailable:
            failed += 1
            await _record_failure(
                session_factory,
                source=source,
                external_id=item.external_id,
                sha256=UNKNOWN_SHA256,
                error_code="item_unavailable",
                now=now,
            )
            logger.warning(
                "sync_item_unavailable", source=source, external_id=item.external_id
            )
            # Design §9: the cursor moves past every item the run looked at,
            # failures included. Recovering them is the bounded replay
            # sweeper's job. Holding the cursor back for a permanently failing
            # item would re-list and re-download it on every run forever, over
            # a window that grows until it truncates at the cap.
            processed.append(item)
            continue

        async with session_factory() as session:
            # The dedupe key is the exact triple (design §7), so the content
            # hash has to be in hand: this is checked after the transfer and
            # before the job, and what it saves is the MinerU parse and the LLM
            # spend that the job would trigger.
            seen = await already_ingested(
                session,
                source=source,
                external_id=item.external_id,
                sha256=fetched.sha256,
            )
            await session.commit()
        if seen:
            skipped += 1
            processed.append(item)
            continue

        try:
            async with session_factory() as session:
                job, created = await ingest_upload(
                    UploadFile(
                        file=io.BytesIO(fetched.content),
                        filename=fetched.filename,
                        size=len(fetched.content),
                    ),
                    db=session,
                    validator=UploadValidator(),
                    scanner=scanner,
                    storage=ResumeStorageService(),
                    jobs=IngestionJobService(session),
                    source=source,
                    source_external_id=item.external_id,
                    jd_code=item.jd_code,
                    actor=actor,
                )
                # `created is False` means an active job already covers these
                # bytes; the ledger still points at it, or the next run would
                # pay to download and parse them all over again.
                await record_item(
                    session,
                    source=source,
                    external_id=item.external_id,
                    sha256=fetched.sha256,
                    outcome="ingested",
                    job_id=job.id,
                    now=now,
                )
                await session.commit()
        except UploadValidationError:
            # Design §16.3: recruitment attachments are not guaranteed to be
            # formats WP1 accepts. A rejected file is one bad item, not a
            # reason to abandon the batch.
            failed += 1
            await _record_failure(
                session_factory,
                source=source,
                external_id=item.external_id,
                sha256=fetched.sha256,
                error_code="unsupported_attachment",
                now=now,
            )
            # Design §9: see the `item_unavailable` branch above.
            processed.append(item)
            continue
        except Exception as exc:
            failed += 1
            await _record_failure(
                session_factory,
                source=source,
                external_id=item.external_id,
                sha256=fetched.sha256,
                error_code="ingestion_failed",
                now=now,
            )
            logger.error(
                "sync_item_ingestion_failed",
                source=source,
                external_id=item.external_id,
                error_type=type(exc).__name__,
            )
            # Design §9: see the `item_unavailable` branch above.
            processed.append(item)
            continue

        if created:
            # A reused job already has a message in flight; a second one would
            # parse the same file twice.
            enqueue_job(job.id)
        ingested += 1
        processed.append(item)

    advanced = next_cursor(cursor, processed)

    async with session_factory() as session:
        await write_cursor(session, source, value=advanced, now=now)
        session.add(
            AuditLog(
                event_type="resume_sync_completed",
                actor=actor,
                target_type="sync",
                payload={
                    "source": source,
                    "cursor_from": cursor.isoformat(),
                    "cursor_to": advanced.isoformat(),
                    "listed": len(items),
                    "ingested": ingested,
                    "skipped": skipped,
                    "failed": failed,
                    "truncated": truncated,
                    "dropped_at_least": dropped_at_least,
                },
            )
        )
        await session.commit()

    logger.info(
        "resume_sync_completed",
        source=source,
        listed=len(items),
        ingested=ingested,
        skipped=skipped,
        failed=failed,
        truncated=truncated,
        dropped_at_least=dropped_at_least,
    )
    return SyncReport(
        listed=len(items),
        ingested=ingested,
        skipped=skipped,
        failed=failed,
        dropped_at_least=dropped_at_least,
        truncated=truncated,
        cursor_from=cursor,
        cursor_to=advanced,
    )


async def _record_failure(
    session_factory: SessionFactory,
    *,
    source: str,
    external_id: str,
    sha256: str,
    error_code: str,
    now: datetime,
) -> None:
    async with session_factory() as session:
        await record_item(
            session,
            source=source,
            external_id=external_id,
            sha256=sha256,
            outcome="failed",
            error_code=error_code,
            now=now,
        )
        await session.commit()


async def _audit(
    session_factory: SessionFactory,
    *,
    event_type: str,
    actor: str,
    payload: dict,
) -> None:
    """Record a run-level event.

    Payloads carry counts, cursors, and error codes only — never a filename, an
    object key, or anything a candidate supplied.
    """
    async with session_factory() as session:
        session.add(
            AuditLog(
                event_type=event_type,
                actor=actor,
                target_type="sync",
                payload=payload,
            )
        )
        await session.commit()
