from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from backend.app.config import get_settings
from backend.app.database import AsyncSessionLocal, engine
from backend.app.tasks.celery_app import celery_app


@celery_app.task(name="sync.pull_dingtalk")
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


@celery_app.task(name="sync.replay_failed")
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
