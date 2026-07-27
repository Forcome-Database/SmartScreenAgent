from __future__ import annotations

import io
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

import structlog
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile

from backend.app.config import get_settings
from backend.app.models import JD, AuditLog
from backend.app.services.ingestion.intake import enqueue_job, ingest_upload
from backend.app.services.ingestion.jobs import IngestionJobService
from backend.app.services.storage import ResumeStorageService
from backend.app.services.sync.adapter import (
    ItemUnavailable,
    JobSourceAdapter,
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

    A run that cannot finish writes `resume_sync_failed` with an error code and
    the counts it got to, then re-raises — whether it died at the listing or
    part-way through the batch. Either way it leaves a record: a run that
    aborted must never be indistinguishable from a tick that never fired.
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

    try:
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
    except Exception as exc:
        # Everything above this point can have committed items and published
        # Celery messages already, so an abort here is not "the run did not
        # happen": it is a partial run whose cursor was never written. Without
        # this row it leaves no `resume_sync_*` evidence at all — neither
        # completed nor failed — and an operator cannot tell it from a Beat
        # tick that never fired. The cursor stays unwritten deliberately: the
        # next run re-opens the same window and the ledger deduplicates
        # whatever already landed.
        await _audit(
            session_factory,
            event_type="resume_sync_failed",
            actor=actor,
            payload={
                "source": source,
                "cursor_from": cursor.isoformat(),
                "listed": len(items),
                "ingested": ingested,
                "skipped": skipped,
                "failed": failed,
                "error_code": "run_aborted",
            },
        )
        logger.error(
            "resume_sync_failed",
            source=source,
            error_code="run_aborted",
            error_type=type(exc).__name__,
        )
        raise

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


async def sync_jd_metadata(
    session_factory: SessionFactory, adapter: JobSourceAdapter, *, now: datetime
) -> int:
    """Create missing JDs and refresh their descriptive fields only.

    Design §10: permitted to create a JD that does not exist and to update
    `name`/`description`; forbidden to touch `jds.active_rule_version_id`,
    `jds.status`, or `rule_versions.schema_json`. WP6c gates rule publication
    behind draft -> What-If -> recorded regression -> publish; a background
    task able to move the active rule version, or flip a JD's status, would be
    a back door around that gate.

    That boundary is structural, not a promise kept by omission:

    - `JobMeta` — the adapter's own return type — has no `status` and no
      `active_rule_version_id` field. There is nothing forbidden for this
      function to even read off of it.
    - Both writes below are Core constructs with an explicit `.values()` —
      never a loaded `JD` ORM instance with every mapped column, forbidden ones
      included, sitting on it ready to be set by a stray line added elsewhere
      in the function. The update names exactly `name` and `description`; the
      insert names exactly `code`, `name`, `description` and leaves `status` to
      the column's own `default="active"`, so neither statement can name a
      governed column without someone widening this one `.values()` call in
      full view of a diff.

    The create is `INSERT ... ON CONFLICT (code) DO NOTHING`, not a plain
    `session.add`, because `AsyncSessionLocal` is `autoflush=False`: a `JD`
    added on one iteration is still pending and invisible to the next
    iteration's `SELECT`, so one duplicated `jobCode` in a page would take the
    create branch twice and blow the batch up on `jds.code` at commit —
    rolling back every unrelated JD's legitimate rename, and doing it again on
    every tick forever, because the next run re-lists the same duplicate. The
    same statement closes the concurrent-tick race, where two overlapping runs
    both see `existing is None`.

    `now` matches the signature style of `run_sync` for a caller that wants a
    stable instant to pass through; the audit rows below carry no timestamp of
    their own (`AuditLog.created_at` is server-set), so it is not read.

    `adapter.list_jobs()` is the only network call this function makes, and it
    runs before the session below is opened — no business transaction is ever
    held across it, matching every other WP8 sync entry point.
    """
    source = adapter.source_name
    actor = f"system:sync:{source}"
    try:
        jobs = await adapter.list_jobs()
    except SourceUnavailable:
        await _audit(
            session_factory,
            event_type="jd_sync_failed",
            actor=actor,
            payload={"source": source, "error_code": "source_unavailable"},
        )
        logger.error("jd_sync_failed", source=source, error_code="source_unavailable")
        raise

    changed = 0
    try:
        async with session_factory() as session:
            for job in jobs:
                existing = (
                    await session.execute(
                        select(JD.id, JD.name, JD.description).where(JD.code == job.code)
                    )
                ).one_or_none()
                if existing is None:
                    created = await session.execute(
                        pg_insert(JD)
                        .values(
                            code=job.code,
                            name=job.name,
                            description=job.description,
                        )
                        .on_conflict_do_nothing(index_elements=["code"])
                        .returning(JD.id)
                    )
                    # No row back means another run inserted this code between
                    # the select and here. Nothing was created and nothing was
                    # changed, so it must not be counted as either.
                    if created.first() is not None:
                        changed += 1
                    continue
                existing_id, existing_name, existing_description = existing
                if (existing_name, existing_description) == (job.name, job.description):
                    continue
                await session.execute(
                    update(JD)
                    .where(JD.id == existing_id)
                    .values(name=job.name, description=job.description)
                )
                changed += 1
            await session.commit()
    except Exception as exc:
        # Without this row a JD sync that dies leaves no evidence at all — not
        # even the distinction between "the tick never fired" and "the tick
        # fired and the write blew up". Unlike `run_sync`, the whole batch is
        # one transaction, so the abort rolled everything back; the count is
        # how far the run got, which is why it is not named `changed`.
        await _audit(
            session_factory,
            event_type="jd_sync_failed",
            actor=actor,
            payload={
                "source": source,
                "listed": len(jobs),
                "changed_before_abort": changed,
                "error_code": "run_aborted",
            },
        )
        logger.error(
            "jd_sync_failed",
            source=source,
            error_code="run_aborted",
            error_type=type(exc).__name__,
        )
        raise

    await _audit(
        session_factory,
        event_type="jd_sync_completed",
        actor=actor,
        payload={"source": source, "listed": len(jobs), "changed": changed},
    )
    logger.info("jd_sync_completed", source=source, listed=len(jobs), changed=changed)
    return changed


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
