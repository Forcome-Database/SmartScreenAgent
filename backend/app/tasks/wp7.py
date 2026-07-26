from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from backend.app.config import get_settings
from backend.app.database import AsyncSessionLocal, engine
from backend.app.models import LLMUsageAttempt
from backend.app.services.llm.usage import abandon_stale_attempts
from backend.app.services.operations.budgets import (
    evaluate_current_budgets,
    reconcile_budgets,
)
from backend.app.tasks.celery_app import celery_app


@celery_app.task(name="wp7.evaluate_budget_attempt")
def evaluate_budget_attempt(attempt_id: int) -> dict:
    """Low-latency budget evaluation for the periods containing one attempt."""

    async def _runner() -> dict:
        try:
            async with AsyncSessionLocal() as db:
                attempt = await db.get(LLMUsageAttempt, attempt_id)
                if attempt is None:
                    return {"attempt_id": attempt_id, "evaluated": False}
                evaluations = await evaluate_current_budgets(db, now=attempt.started_at)
                await db.commit()
                return {
                    "attempt_id": attempt_id,
                    "evaluated": True,
                    "states": {item.scope: item.state for item in evaluations},
                }
        finally:
            await engine.dispose()

    return asyncio.run(_runner())


@celery_app.task(name="wp7.reconcile_budgets")
def reconcile_budgets_task() -> dict:
    """Durable delivery: drain any crossing missed while the app was down."""

    async def _runner() -> dict:
        settings = get_settings()
        try:
            async with AsyncSessionLocal() as db:
                report = await reconcile_budgets(
                    db,
                    now=datetime.now(UTC),
                    max_periods=settings.LLM_BUDGET_RECONCILE_MAX_PERIODS_PER_RUN,
                )
                await db.commit()
                return {
                    "processed": report.processed,
                    "states": {item.scope: item.state for item in report.current},
                }
        finally:
            await engine.dispose()

    return asyncio.run(_runner())


@celery_app.task(name="wp7.sweep_stale_usage")
def sweep_stale_usage() -> dict:
    """Close out pending ledger rows whose finalization never arrived."""

    async def _runner() -> dict:
        settings = get_settings()
        try:
            async with AsyncSessionLocal() as db:
                cutoff = datetime.now(UTC) - timedelta(
                    seconds=settings.LLM_USAGE_PENDING_TIMEOUT_SECONDS
                )
                abandoned = await abandon_stale_attempts(db, older_than=cutoff)
                await db.commit()
                return {"abandoned": len(abandoned)}
        finally:
            await engine.dispose()

    return asyncio.run(_runner())
