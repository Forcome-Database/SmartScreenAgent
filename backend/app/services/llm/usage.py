from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, cast
from uuid import UUID

from sqlalchemy import func, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.database import AsyncSessionLocal
from backend.app.models import LLMUsageAttempt
from backend.app.services.llm.errors import UsageLedgerUnavailable
from backend.app.services.llm.pricing import ModelPrice, PriceBook, estimate_cost, parse_price_book

logger = logging.getLogger(__name__)

Operation = Literal["extract", "judge", "cross_check", "lightweight"]
AttemptRole = Literal["primary", "fallback", "secondary"]
TerminalStatus = Literal[
    "succeeded",
    "unavailable",
    "invalid_response",
    "configuration_error",
    "abandoned",
]
SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]
EnqueueHook = Callable[[int], None]


def _enqueue_budget_evaluation(attempt_id: int) -> None:
    """Ask a worker to re-evaluate budgets for the period containing this attempt.

    Imported lazily: `tasks.celery_app` reads settings at import time and the
    task module imports this one.
    """
    from backend.app.tasks.celery_app import celery_app

    celery_app.send_task("wp7.evaluate_budget_attempt", args=[attempt_id])

_TERMINAL_STATUSES = {
    "succeeded",
    "unavailable",
    "invalid_response",
    "configuration_error",
    "abandoned",
}
_MAX_INTEGER = 2_147_483_647


@dataclass(frozen=True)
class LLMCallContext:
    operation: Operation
    call_group_id: UUID
    trace_id: str | None = None
    ingestion_job_id: int | None = None
    score_id: int | None = None
    jd_id: int | None = None
    rule_version_id: int | None = None


@dataclass(frozen=True)
class UsageAttemptHandle:
    attempt_id: int
    price: ModelPrice
    started_at: datetime
    trace_id: str | None


class UsageRecorder:
    def __init__(
        self,
        *,
        session_factory: SessionFactory = AsyncSessionLocal,
        prices: PriceBook | None = None,
        finalize_retries: int | None = None,
        enqueue: EnqueueHook | None = None,
    ) -> None:
        if prices is None or finalize_retries is None:
            from backend.app.config import get_settings

            settings = get_settings()
            if prices is None:
                prices = parse_price_book(settings.LLM_PRICE_CNY_PER_MILLION_JSON)
            if finalize_retries is None:
                finalize_retries = settings.LLM_USAGE_FINALIZE_MAX_RETRIES
        if type(finalize_retries) is not int or finalize_retries < 1:
            raise ValueError("finalize_retries must be a positive integer")
        self._session_factory = session_factory
        self._prices = prices
        self._finalize_retries = finalize_retries
        self._enqueue = enqueue or _enqueue_budget_evaluation

    async def begin(
        self,
        *,
        context: LLMCallContext,
        requested_model: str,
        attempt_role: AttemptRole,
        prompt_version: str,
    ) -> UsageAttemptHandle:
        price = self._prices.require(requested_model)
        started_at = datetime.now(timezone.utc)
        attempt = LLMUsageAttempt(
            call_group_id=context.call_group_id,
            trace_id=context.trace_id,
            ingestion_job_id=context.ingestion_job_id,
            score_id=context.score_id,
            jd_id=context.jd_id,
            rule_version_id=context.rule_version_id,
            operation=context.operation,
            attempt_role=attempt_role,
            requested_model=requested_model,
            actual_model=None,
            prompt_version=prompt_version,
            status="pending",
            input_tokens=None,
            output_tokens=None,
            input_price_cny_per_million=price.input_cny_per_million,
            output_price_cny_per_million=price.output_cny_per_million,
            estimated_cost_cny=None,
            latency_ms=None,
            error_code=None,
            started_at=started_at,
            finished_at=None,
        )
        try:
            async with self._session_factory() as session:
                session.add(attempt)
                await session.flush()
                attempt_id = attempt.id
                await session.commit()
        except (SQLAlchemyError, OSError) as exc:
            logger.critical(
                "llm usage pending insert failed",
                extra={
                    "trace_id": context.trace_id,
                    "operation": context.operation,
                    "exception_class": type(exc).__name__,
                },
            )
            raise UsageLedgerUnavailable("usage ledger unavailable") from None
        return UsageAttemptHandle(
            attempt_id=attempt_id,
            price=price,
            started_at=started_at,
            trace_id=context.trace_id,
        )

    async def finalize(
        self,
        handle: UsageAttemptHandle,
        *,
        status: TerminalStatus,
        actual_model: str | None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        latency_ms: int | None = None,
        error_code: str | None = None,
    ) -> bool:
        try:
            self._validate_terminal_values(
                status=status,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=latency_ms,
            )
            cost = estimate_cost(handle.price, input_tokens, output_tokens)
        except Exception as exc:  # paid calls must not escape through accounting
            self._log_finalization_failure(handle, retry_count=0, exc=exc)
            return False

        last_exception: Exception | None = None
        for _ in range(self._finalize_retries):
            try:
                async with self._session_factory() as session:
                    result = cast(
                        CursorResult[object],
                        await session.execute(
                            update(LLMUsageAttempt)
                            .where(
                                LLMUsageAttempt.id == handle.attempt_id,
                                LLMUsageAttempt.status == "pending",
                            )
                            .values(
                                actual_model=actual_model,
                                input_tokens=input_tokens,
                                output_tokens=output_tokens,
                                estimated_cost_cny=cost,
                                latency_ms=latency_ms,
                                error_code=error_code,
                                finished_at=func.now(),
                                status=status,
                            )
                        ),
                    )
                    await session.commit()
                    updated = result.rowcount == 1
            except Exception as exc:
                last_exception = exc
                continue
            if updated:
                self._notify_budget_evaluation(handle)
            return updated

        assert last_exception is not None
        self._log_finalization_failure(
            handle,
            retry_count=self._finalize_retries,
            exc=last_exception,
        )
        return False

    def _notify_budget_evaluation(self, handle: UsageAttemptHandle) -> None:
        """Best-effort budget notification.

        The call is already paid for and the ledger row is already terminal, so a
        dead broker must not change what `finalize` reports. The cursor-based
        reconciler is the durable path; this is only the low-latency one.
        """
        try:
            self._enqueue(handle.attempt_id)
        except Exception as exc:
            logger.warning(
                "llm budget evaluation enqueue failed",
                extra={
                    "attempt_id": handle.attempt_id,
                    "trace_id": handle.trace_id,
                    "exception_class": type(exc).__name__,
                },
            )

    @staticmethod
    def _validate_terminal_values(
        *,
        status: str,
        input_tokens: int | None,
        output_tokens: int | None,
        latency_ms: int | None,
    ) -> None:
        if status not in _TERMINAL_STATUSES:
            raise ValueError("invalid terminal status")
        for name, value in (
            ("input_tokens", input_tokens),
            ("output_tokens", output_tokens),
            ("latency_ms", latency_ms),
        ):
            if value is not None and (
                type(value) is not int or not 0 <= value <= _MAX_INTEGER
            ):
                raise ValueError(f"invalid {name}")

    @staticmethod
    def _log_finalization_failure(
        handle: UsageAttemptHandle,
        *,
        retry_count: int,
        exc: Exception,
    ) -> None:
        logger.critical(
            "llm usage finalization failed",
            extra={
                "attempt_id": handle.attempt_id,
                "trace_id": handle.trace_id,
                "retry_count": retry_count,
                "exception_class": type(exc).__name__,
            },
        )


async def abandon_stale_attempts(
    db: AsyncSession,
    *,
    older_than: datetime,
) -> list[int]:
    result = await db.execute(
        update(LLMUsageAttempt)
        .where(
            LLMUsageAttempt.status == "pending",
            LLMUsageAttempt.started_at < older_than,
        )
        .values(
            status="abandoned",
            finished_at=func.now(),
            error_code="usage_finalization_missing",
        )
        .returning(LLMUsageAttempt.id)
    )
    return list(result.scalars())
