"""Bounded re-drive of failed ledger rows.

Design §9: "A failed item cannot be rediscovered by the cursor — the cursor has
moved past it and the 300-second overlap will not reach back. Failed ledger
rows are therefore re-driven by a bounded sweeper up to
`SYNC_MAX_ITEM_ATTEMPTS`, then made terminal."

This module therefore never lists. It reads `sync_source_items`, asks the
adapter to re-derive each failed item **by its `source_external_id`**, and
re-drives it through the same WP3 intake the runner uses. Re-listing could not
work even in principle: the ledger keeps no source timestamp, only our own
clock, which is always later than the item's — so no `since` derived from it
would return the item.

Asking by id also means recovery does not depend on the source's `updateTime`
having moved, which is the case that matters most: a recruiter attaching the
resume after the fact need not bump the candidate record's timestamp.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import datetime

import structlog
from sqlalchemy import delete, select
from starlette.datastructures import UploadFile

from backend.app.config import get_settings
from backend.app.models import AuditLog, SyncSourceItem
from backend.app.services.ingestion.intake import enqueue_job, ingest_upload
from backend.app.services.ingestion.jobs import IngestionJobService
from backend.app.services.storage import ResumeStorageService
from backend.app.services.sync.adapter import (
    ItemUnavailable,
    ResumeSourceAdapter,
    SourceCapabilityUnavailable,
    SourceUnavailable,
)
from backend.app.services.sync.ledger import already_ingested, record_item
from backend.app.services.sync.runner import SessionFactory
from backend.app.services.upload import UploadValidationError, UploadValidator, get_malware_scanner

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class ReplayReport:
    """What one sweep did.

    `undescribable` is separate from `failed` on purpose. `failed` means the
    source was asked and the item did not come back — an attempt was spent and
    the row is that much closer to terminal. `undescribable` means the source
    was never asked at all, because this adapter has no lookup by id; nothing
    was spent and nothing moved. Folded together, an operator reading
    `replayed: 0, failed: 0` would conclude the queue was clean when in fact it
    was untouched.
    """

    selected: int
    replayed: int
    failed: int
    superseded: int
    undescribable: int


async def replay_failed(
    session_factory: SessionFactory,
    adapter: ResumeSourceAdapter,
    *,
    now: datetime,
    max_attempts: int,
) -> ReplayReport:
    """Re-drive this source's failed items, up to their attempt bound.

    Terminality needs no marker: the selection predicate is
    `attempts < max_attempts`, so a row that reaches the bound simply stops
    matching. `error_code` is left saying why the item actually failed, which
    is the only thing it should say and the one field worth reading when
    somebody finally looks at a stuck row.

    Every database write is its own short transaction, and both network calls
    happen with none open — the same discipline the runner keeps, for the same
    reason: a stalled provider must never hold a connection manual upload needs.

    A sweep that cannot finish — the provider is down, the database refuses, the
    broker refuses — writes `resume_sync_replay_failed` with the counts it got
    to and then re-raises. A sweep that ran and aborted must never be
    indistinguishable from a sweep that never ran.
    """
    source = adapter.source_name
    actor = f"system:sync_replay:{source}"
    settings = get_settings()
    # Built once, before anything is attempted, exactly as the runner does: a
    # misconfigured scan mode is one loud configuration error rather than N
    # `ingestion_failed` rows that also burn N attempts.
    scanner = get_malware_scanner(settings.MALWARE_SCAN_MODE)

    async with session_factory() as session:
        pending = (
            await session.execute(
                select(
                    SyncSourceItem.id,
                    SyncSourceItem.source_external_id,
                    SyncSourceItem.content_sha256,
                )
                .where(
                    # Scoped to this adapter's own source: another source's
                    # external ids mean nothing to this `describe`.
                    SyncSourceItem.source == source,
                    SyncSourceItem.outcome == "failed",
                    SyncSourceItem.attempts < max_attempts,
                )
                .order_by(SyncSourceItem.first_seen_at, SyncSourceItem.id)
            )
        ).all()
        await session.commit()

    selected = len(pending)
    replayed = failed = superseded = undescribable = 0

    async def _audit_abort(error_code: str) -> None:
        """Leave evidence that this sweep ran, even though it did not finish.

        The counters are read at call time, so the row carries what the pass
        actually accomplished before it died. Counts and an error code only —
        never a candidate id, a filename, an object key, or the text of an
        exception, any of which can name a real person. That is the WP8
        `external_id` policy: a `source_external_id` MAY be stored in the ledger
        and MAY appear in a structlog line; it MUST NOT reach an audit payload,
        an API response, or an exception message. See `runner._audit`,
        `dingtalk.py`, and `schemas/sync_report.py` for the other three.

        This row is written through the same `session_factory` that may be WHY
        the sweep aborted, so the write is guarded: unguarded, it would raise
        from inside the `except`, leaving the original exception as nothing but
        a `__context__` and handing Celery the audit-write error as the task's
        failure. The caller re-raises the original either way; a type name and a
        fixed code are logged, never a driver message that can quote a row.
        """
        try:
            async with session_factory() as session:
                session.add(
                    AuditLog(
                        event_type="resume_sync_replay_failed",
                        actor=actor,
                        target_type="sync",
                        payload={
                            "source": source,
                            "selected": selected,
                            "replayed": replayed,
                            "failed": failed,
                            "superseded": superseded,
                            "undescribable": undescribable,
                            "max_attempts": max_attempts,
                            "error_code": error_code,
                        },
                    )
                )
                await session.commit()
        except Exception as exc:
            logger.error(
                "sync_replay_audit_write_failed",
                source=source,
                error_code="audit_write_failed",
                error_type=type(exc).__name__,
            )

    try:
        for index, (row_id, external_id, stored_sha) in enumerate(pending):
            try:
                item = await adapter.describe(external_id)
            except SourceCapabilityUnavailable:
                # A fact about the adapter, not about this item, so the
                # remaining rows are in the same position and asking each one
                # would be pointless. Nothing is recorded: no attempt is spent
                # on a question that was never put to the source.
                undescribable = selected - index
                # `warning`, not `info`: for the only adapter we have this
                # fires on every sweep forever, and it says the recovery
                # mechanism is inert rather than idle.
                logger.warning(
                    "sync_replay_unsupported",
                    source=source,
                    undescribable=undescribable,
                )
                break
            except ItemUnavailable:
                # The source HAS the lookup and answered "not here" — a real
                # failure, bounded like any other.
                failed += 1
                await _spend_attempt(
                    session_factory,
                    source=source,
                    external_id=external_id,
                    sha256=stored_sha,
                    error_code="item_unavailable",
                    now=now,
                )
                continue

            try:
                fetched = await adapter.fetch(item)
            except ItemUnavailable:
                failed += 1
                await _spend_attempt(
                    session_factory,
                    source=source,
                    external_id=external_id,
                    sha256=stored_sha,
                    error_code="item_unavailable",
                    now=now,
                )
                continue

            async with session_factory() as session:
                seen = await already_ingested(
                    session, source=source, external_id=external_id, sha256=fetched.sha256
                )
                await session.commit()
            if seen:
                # Something already ingested these exact bytes for this
                # candidate — most likely the runner, which re-lists a
                # newest-in-window item on every run. The row we selected is
                # `failed`, and the identity triple is unique, so it cannot BE
                # that ingested row: it is a superseded record of an attempt
                # that has since succeeded. Deleting it is safe and necessary —
                # left in place it matches the predicate on every future sweep
                # and re-downloads the same attachment until it exhausts.
                superseded += 1
                await _discard(session_factory, row_id=row_id)
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
                        # The LEDGER's id, not the described item's. They are
                        # the same for any correct adapter, but this is the id
                        # the row was selected by and the id the attempt bound
                        # is keyed to, so the job and the ledger cannot
                        # disagree about which candidate this is even if
                        # `describe` echoes back a different one.
                        source_external_id=external_id,
                        jd_code=item.jd_code,
                        actor=actor,
                    )
                    await record_item(
                        session,
                        source=source,
                        external_id=external_id,
                        sha256=fetched.sha256,
                        outcome="ingested",
                        job_id=job.id,
                        now=now,
                    )
                    if fetched.sha256 != stored_sha:
                        # The row we selected recorded a DIFFERENT content hash
                        # — almost always `UNKNOWN_SHA256`, the placeholder a
                        # fetch failure leaves behind because the bytes were
                        # never seen. `record_item` above therefore wrote a new
                        # row under the real hash and left this one `failed`
                        # and still inside the bound. Its meaning ("this id has
                        # no fetched content") has just stopped being true, and
                        # the durable history of the failure lives in the
                        # `resume_sync_*` audit trail, not in a dedupe ledger.
                        # When the hashes match there is nothing to discard:
                        # `record_item` updated this very row.
                        await session.execute(
                            delete(SyncSourceItem).where(SyncSourceItem.id == row_id)
                        )
                    await session.commit()
            except UploadValidationError:
                failed += 1
                await _spend_attempt(
                    session_factory,
                    source=source,
                    external_id=external_id,
                    sha256=stored_sha,
                    error_code="unsupported_attachment",
                    now=now,
                )
                continue
            except Exception as exc:
                failed += 1
                await _spend_attempt(
                    session_factory,
                    source=source,
                    external_id=external_id,
                    sha256=stored_sha,
                    error_code="ingestion_failed",
                    now=now,
                )
                logger.error(
                    "sync_replay_ingestion_failed",
                    source=source,
                    error_type=type(exc).__name__,
                )
                continue

            if created:
                enqueue_job(job.id)
            replayed += 1

        # INSIDE the guarded region, matching `run_sync`'s completed audit.
        # Outside it, a sweep whose success row failed to write would leave no
        # `resume_sync_*` evidence at all — the one outcome both branches of
        # this handler exist to make impossible.
        async with session_factory() as session:
            session.add(
                AuditLog(
                    event_type="resume_sync_replayed",
                    actor=actor,
                    target_type="sync",
                    payload={
                        "source": source,
                        "selected": selected,
                        "replayed": replayed,
                        "failed": failed,
                        "superseded": superseded,
                        "undescribable": undescribable,
                        "max_attempts": max_attempts,
                    },
                )
            )
            await session.commit()
    except SourceUnavailable:
        # The provider is down — true of every row and of none in particular.
        # No attempt is spent, here or on the rows not yet reached: the pass is
        # abandoned exactly as `run_sync` abandons a run whose `list_changed`
        # raises, and for the same reason. Spending one attempt per row per
        # sweep during an outage would drive the whole failed queue terminal in
        # about `SYNC_MAX_ITEM_ATTEMPTS` sweeps without a single genuine
        # per-item failure. Rows already processed in this pass keep whatever
        # they earned.
        await _audit_abort("source_unavailable")
        logger.error("resume_sync_replay_failed", source=source, error_code="source_unavailable")
        raise
    except Exception as exc:
        # Anything else — a database error, a broker error, a bug. The sweep is
        # over either way, and the one thing that must not also be lost is the
        # evidence that it ran: an operator has to be able to tell an aborted
        # sweep from a sweep that never happened, and Celery's failed-task
        # record is not the audit trail this design specifies.
        await _audit_abort("replay_aborted")
        logger.error(
            "resume_sync_replay_failed",
            source=source,
            error_code="replay_aborted",
            error_type=type(exc).__name__,
        )
        raise

    logger.info(
        "resume_sync_replayed",
        source=source,
        selected=selected,
        replayed=replayed,
        failed=failed,
        superseded=superseded,
        undescribable=undescribable,
    )
    return ReplayReport(
        selected=selected,
        replayed=replayed,
        failed=failed,
        superseded=superseded,
        undescribable=undescribable,
    )


async def _spend_attempt(
    session_factory: SessionFactory,
    *,
    source: str,
    external_id: str,
    sha256: str,
    error_code: str,
    now: datetime,
) -> None:
    """Bump the attempt count on the row that was selected.

    Keyed on the STORED hash, never on a freshly fetched one. `record_item`
    upserts on the identity triple, so recording a failure under a new hash
    would create a second row and leave the selected one at its old count —
    the bound would never be reached and the sweeper would retry it forever.
    """
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


async def _discard(session_factory: SessionFactory, *, row_id: int) -> None:
    async with session_factory() as session:
        await session.execute(delete(SyncSourceItem).where(SyncSourceItem.id == row_id))
        await session.commit()
