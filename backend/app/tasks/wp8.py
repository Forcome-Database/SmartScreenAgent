from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from backend.app.config import (
    SYNC_HARD_TIME_LIMIT_SECONDS,
    SYNC_SOFT_TIME_LIMIT_SECONDS,
    get_settings,
)
from backend.app.database import AsyncSessionLocal, engine
from backend.app.tasks.celery_app import celery_app

# Why these three tasks override Celery's global 600 s `task_time_limit`.
#
# `SYNC_MAX_ITEMS_PER_RUN` is 200, and each item costs an HTTP download (30 s
# timeout of its own), a temp-file write, a PDF/DOCX parse, a MinIO upload and
# three DB round trips. A slow provider puts one full run well past 600 s — and
# Celery's HARD limit kills the child process, so no `except Exception` runs at
# all: no `resume_sync_failed` row and no cursor write. That is precisely the
# state this package's audit discipline exists to prevent, a partial run
# indistinguishable from a tick that never fired, made worse by the operator
# README teaching people to read a non-moving `cursor_value` as "sync is
# working".
#
# The GLOBAL limit stays at 600 s. Its comment in `celery_app.py` requires it to
# stay below `INGESTION_LEASE_SECONDS` (900) so a task is never force-killed
# while its ingestion job's lease still reads as live and the sweeper could
# reclaim a job the worker still owns. That reasoning does not reach these
# tasks: they CREATE ingestion jobs, they never hold an ingestion lease. The
# lease is claimed inside `ingest.parse_and_score`
# (`IngestionJobService.claim(job_id, lease_seconds=INGESTION_LEASE_SECONDS)`),
# a different task still under the global limit, and every job created here is
# committed and handed to `enqueue_job` immediately, so no job's `queued` age is
# tied to how long this run takes either.
#
# SOFT strictly below HARD is the whole point: `SoftTimeLimitExceeded` subclasses
# `Exception`, so it propagates into the abort handlers in `runner.py` and
# `replay.py` and the audit row actually gets written. The 240 s between the two
# is the budget that write is given before the process is killed outright.
#
# DEPLOYMENT CONSTRAINT on everything the paragraph above claims. Celery delivers
# a soft limit as `SIGUSR1`, so it is a POSIX-signal feature: on Windows Celery
# ignores `soft_time_limit` outright, and even on Linux the signal only reaches
# the task under the PREFORK pool — `--pool=solo` and `--pool=threads` do not
# deliver it either. Run the worker anywhere in that set and
# `SoftTimeLimitExceeded` is never raised, no abort handler runs, and the
# audit-on-timeout guarantee is silently absent while the numbers below still
# read as if it held. Note that `docker-compose.yml` deliberately does NOT
# contain worker or beat services (see its comment): the README quick start runs
# them on the host with the default pool, so "the deployment host is Linux
# prefork" is a requirement of this file, not something the repo enforces.
#
# Both limits sit below `DINGTALK_SYNC_INTERVAL_SECONDS` so a run that hangs is
# dead before Beat publishes the next tick and runs cannot pile up. That is no
# longer a hope about the default: `Settings` refuses to construct when the
# interval does not exceed the hard limit while `DINGTALK_SYNC_ENABLED` is true.
# The two constants are declared in `backend/app/config.py` for that reason —
# the validator needs them, and `config.py` cannot import this module without a
# cycle. They are imported here because these decorators are what they are FOR.


@celery_app.task(
    name="sync.pull_dingtalk",
    soft_time_limit=SYNC_SOFT_TIME_LIMIT_SECONDS,
    time_limit=SYNC_HARD_TIME_LIMIT_SECONDS,
)
def pull_dingtalk_task() -> dict:
    """Pull one batch of changed recruitment resumes."""

    async def _runner() -> dict:
        settings = get_settings()
        # Second line of defence. The Beat entry is not registered at all while
        # the switch is off (see `celery_app.py`), so nothing schedules this —
        # but a manual `send_task` still could, and design §5.2 says no adapter
        # is constructed while sync is disabled.
        if not settings.DINGTALK_SYNC_ENABLED:
            return {"skipped": "disabled"}

        from backend.app.services.sync.dingtalk import DingTalkRecruitmentAdapter
        from backend.app.services.sync.runner import run_sync

        try:
            # No token argument: the adapter mints and caches its own corp
            # token via `DingTalkCorpTokenClient`. The `access_token=`
            # parameter exists only for the operator-driven live probe.
            report = await run_sync(
                AsyncSessionLocal,
                DingTalkRecruitmentAdapter(),
                now=datetime.now(timezone.utc),
                overlap_seconds=settings.SYNC_OVERLAP_SECONDS,
                max_items=settings.SYNC_MAX_ITEMS_PER_RUN,
            )
        finally:
            # The session never leaves this task: the worker pool is reused
            # across tasks and a connection bound to this run's event loop
            # would be handed to the next one on a different loop.
            await engine.dispose()
        return {
            "listed": report.listed,
            "ingested": report.ingested,
            "skipped": report.skipped,
            "failed": report.failed,
            "dropped_at_least": report.dropped_at_least,
            "truncated": report.truncated,
        }

    return asyncio.run(_runner())


@celery_app.task(
    name="sync.pull_jds",
    soft_time_limit=SYNC_SOFT_TIME_LIMIT_SECONDS,
    time_limit=SYNC_HARD_TIME_LIMIT_SECONDS,
)
def pull_jds_task() -> dict:
    """Create missing JDs and refresh their name/description from the source.

    Deliberately its own task with its own Beat entry, not a step chained onto
    `sync.pull_dingtalk`. `JOBS_PATH` is a separate unverified endpoint that may
    not be permission-granted even when the candidates one is, and `list_jobs`
    maps every failure to `SourceUnavailable`. Chained in-process, a standing
    403 on jobs would take resume ingestion down with it indefinitely — the
    exact opposite of this package's exit gate, that sync is additive and never
    load-bearing.
    """

    async def _runner() -> dict:
        settings = get_settings()
        # Same second line of defence as the pull above: the Beat entry is
        # absent while the switch is off, but a manual `send_task` is not.
        if not settings.DINGTALK_SYNC_ENABLED:
            return {"skipped": "disabled"}

        from backend.app.services.sync.dingtalk import DingTalkRecruitmentAdapter
        from backend.app.services.sync.runner import sync_jd_metadata

        try:
            changed = await sync_jd_metadata(
                AsyncSessionLocal,
                DingTalkRecruitmentAdapter(),
                now=datetime.now(timezone.utc),
            )
        finally:
            await engine.dispose()
        return {"changed": changed}

    return asyncio.run(_runner())


@celery_app.task(
    name="sync.replay_failed",
    soft_time_limit=SYNC_SOFT_TIME_LIMIT_SECONDS,
    time_limit=SYNC_HARD_TIME_LIMIT_SECONDS,
)
def replay_failed_task() -> dict:
    """Re-drive failed ledger items by external id, up to their attempt bound."""

    async def _runner() -> dict:
        settings = get_settings()
        if not settings.DINGTALK_SYNC_ENABLED:
            return {"skipped": "disabled"}

        from backend.app.services.sync.dingtalk import DingTalkRecruitmentAdapter
        from backend.app.services.sync.replay import replay_failed

        try:
            report = await replay_failed(
                AsyncSessionLocal,
                DingTalkRecruitmentAdapter(),
                now=datetime.now(timezone.utc),
                max_attempts=settings.SYNC_MAX_ITEM_ATTEMPTS,
            )
        finally:
            await engine.dispose()
        return {
            "selected": report.selected,
            "replayed": report.replayed,
            "failed": report.failed,
            "superseded": report.superseded,
            # Never folded into `failed`: it means the source was never asked,
            # so an operator seeing zeros elsewhere knows the queue is
            # untouched rather than clean.
            "undescribable": report.undescribable,
        }

    return asyncio.run(_runner())
