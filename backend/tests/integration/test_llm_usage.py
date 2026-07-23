from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from openai import APIConnectionError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.database import AsyncSessionLocal, engine
from backend.app.models import LLMUsageAttempt
from backend.app.services.llm.errors import ModelPriceMissing, UsageLedgerUnavailable
from backend.app.services.llm.gateway import LLMGateway
from backend.app.services.llm.pricing import PriceBook, parse_price_book
from backend.app.services.llm.usage import (
    LLMCallContext,
    UsageRecorder,
    abandon_stale_attempts,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _prices() -> PriceBook:
    return parse_price_book(
        '{"test-judge":{"input":1,"output":2},'
        '"test-judge-fallback":{"input":1,"output":2}}'
    )


def _context() -> LLMCallContext:
    return LLMCallContext(
        operation="judge",
        call_group_id=uuid4(),
        trace_id="trace-1",
        ingestion_job_id=None,
        score_id=None,
        jd_id=None,
        rule_version_id=None,
    )


async def _load_attempt(db_session: AsyncSession, attempt_id: int) -> LLMUsageAttempt:
    attempt = await db_session.get(LLMUsageAttempt, attempt_id, populate_existing=True)
    assert attempt is not None
    return attempt


async def _begin(recorder: UsageRecorder | None = None):
    active_recorder = recorder or UsageRecorder(prices=_prices())
    handle = await active_recorder.begin(
        context=_context(),
        requested_model="test-judge",
        attempt_role="primary",
        prompt_version="judge-v1",
    )
    return active_recorder, handle


async def test_call_context_optional_fields_default_to_none():
    context = LLMCallContext(operation="judge", call_group_id=uuid4())

    assert context.trace_id is None
    assert context.ingestion_job_id is None
    assert context.score_id is None
    assert context.jd_id is None
    assert context.rule_version_id is None


async def test_begin_and_finalize_success_records_exact_content_free_usage(db_session):
    recorder, handle = await _begin()

    finalized = await recorder.finalize(
        handle,
        status="succeeded",
        actual_model="provider-versioned-judge",
        input_tokens=10,
        output_tokens=5,
        latency_ms=123,
    )

    assert finalized is True
    row = await _load_attempt(db_session, handle.attempt_id)
    assert row.status == "succeeded"
    assert row.actual_model == "provider-versioned-judge"
    assert row.input_tokens == 10
    assert row.output_tokens == 5
    assert row.latency_ms == 123
    assert row.estimated_cost_cny == Decimal("0.000020000000")
    assert row.finished_at is not None
    columns = set(LLMUsageAttempt.__table__.columns.keys())
    assert {"prompt", "response", "candidate_name", "object_key"}.isdisjoint(columns)


async def test_begin_returns_flushed_id_with_expiring_session_factory(db_session):
    expiring_sessions = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=True,
        autoflush=False,
    )
    recorder = UsageRecorder(session_factory=expiring_sessions, prices=_prices())

    _, handle = await _begin(recorder)

    assert handle.attempt_id > 0
    assert (await _load_attempt(db_session, handle.attempt_id)).status == "pending"


async def test_terminal_attempt_cannot_be_finalized_twice(db_session):
    recorder, handle = await _begin()

    assert await recorder.finalize(
        handle,
        status="unavailable",
        actual_model=None,
        error_code="provider_unavailable",
        latency_ms=19,
    )
    original = await _load_attempt(db_session, handle.attempt_id)
    original_finished_at = original.finished_at
    assert not await recorder.finalize(
        handle,
        status="succeeded",
        actual_model="late-model",
        input_tokens=1,
        output_tokens=1,
        latency_ms=20,
    )

    row = await _load_attempt(db_session, handle.attempt_id)
    assert row.status == "unavailable"
    assert row.error_code == "provider_unavailable"
    assert row.actual_model is None
    assert row.input_tokens is None
    assert row.output_tokens is None
    assert row.estimated_cost_cny is None
    assert row.latency_ms == 19
    assert row.finished_at == original_finished_at


async def test_begin_requires_price_before_opening_database_and_fails_closed(
    db_session, caplog: pytest.LogCaptureFixture
):
    factory_calls = 0

    def counting_factory() -> Any:
        nonlocal factory_calls
        factory_calls += 1
        raise AssertionError("database must not open without a price")

    recorder = UsageRecorder(session_factory=counting_factory, prices=_prices())
    with pytest.raises(ModelPriceMissing):
        await recorder.begin(
            context=_context(),
            requested_model="unpriced-paid-model",
            attempt_role="primary",
            prompt_version="judge-v1",
        )
    assert factory_calls == 0

    class FailingContext:
        async def __aenter__(self):
            raise OSError("private database details")

        async def __aexit__(self, *args: object) -> None:
            return None

    caplog.clear()
    with caplog.at_level(logging.CRITICAL), pytest.raises(
        UsageLedgerUnavailable
    ) as exc_info:
        await UsageRecorder(session_factory=FailingContext, prices=_prices()).begin(
            context=_context(),
            requested_model="test-judge",
            attempt_role="primary",
            prompt_version="judge-v1",
        )
    assert "private" not in str(exc_info.value)
    critical = [record for record in caplog.records if record.levelno == logging.CRITICAL]
    assert len(critical) == 1
    assert critical[0].trace_id == "trace-1"
    assert critical[0].operation == "judge"
    assert critical[0].exception_class == "OSError"
    assert "private" not in caplog.text


async def test_finalize_retries_fresh_sessions_then_safely_logs_once(
    db_session, caplog: pytest.LogCaptureFixture
):
    good_recorder, handle = await _begin()
    contexts: list[object] = []

    class FailingContext:
        async def __aenter__(self):
            raise OSError("private response or database details")

        async def __aexit__(self, *args: object) -> None:
            return None

    def failing_factory() -> FailingContext:
        context = FailingContext()
        contexts.append(context)
        return context

    recorder = UsageRecorder(
        session_factory=failing_factory,
        prices=_prices(),
        finalize_retries=3,
    )
    with caplog.at_level(logging.CRITICAL):
        finalized = await recorder.finalize(
            handle,
            status="succeeded",
            actual_model="provider-versioned-judge",
            input_tokens=10,
            output_tokens=5,
            latency_ms=123,
        )

    assert finalized is False
    assert len(contexts) == 3
    assert len({id(context) for context in contexts}) == 3
    row = await _load_attempt(db_session, handle.attempt_id)
    assert row.status == "pending"
    assert row.finished_at is None
    critical = [record for record in caplog.records if record.levelno == logging.CRITICAL]
    assert len(critical) == 1
    record = critical[0]
    assert record.attempt_id == handle.attempt_id
    assert record.trace_id == "trace-1"
    assert record.retry_count == 3
    assert record.exception_class == "OSError"
    assert "private" not in caplog.text


async def test_finalize_retries_plain_session_factory_exception_without_raising(
    db_session, caplog: pytest.LogCaptureFixture
):
    _, handle = await _begin()
    factory_calls = 0

    def failing_factory() -> Any:
        nonlocal factory_calls
        factory_calls += 1
        raise RuntimeError("private paid response details")

    recorder = UsageRecorder(
        session_factory=failing_factory,
        prices=_prices(),
        finalize_retries=3,
    )
    with caplog.at_level(logging.CRITICAL):
        finalized = await recorder.finalize(
            handle,
            status="succeeded",
            actual_model="paid-provider-version",
            input_tokens=10,
            output_tokens=5,
            latency_ms=123,
        )

    assert finalized is False
    assert factory_calls == 3
    row = await _load_attempt(db_session, handle.attempt_id)
    assert row.status == "pending"
    assert row.finished_at is None
    critical = [record for record in caplog.records if record.levelno == logging.CRITICAL]
    assert len(critical) == 1
    record = critical[0]
    assert record.attempt_id == handle.attempt_id
    assert record.trace_id == "trace-1"
    assert record.retry_count == 3
    assert record.exception_class == "RuntimeError"
    assert "private" not in caplog.text


async def test_requested_price_snapshot_is_kept_for_provider_versioned_model(db_session):
    recorder, handle = await _begin()

    assert await recorder.finalize(
        handle,
        status="succeeded",
        actual_model="test-judge-2026-07-23",
        input_tokens=1,
        output_tokens=1,
        latency_ms=1,
    )

    row = await _load_attempt(db_session, handle.attempt_id)
    assert row.requested_model == "test-judge"
    assert row.actual_model == "test-judge-2026-07-23"
    assert row.input_price_cny_per_million == Decimal("1.000000")
    assert row.output_price_cny_per_million == Decimal("2.000000")
    assert row.estimated_cost_cny == Decimal("0.000003000000")


async def test_abandon_stale_attempts_only_marks_old_pending_rows(db_session):
    recorder, stale = await _begin()
    _, fresh = await _begin(recorder)
    _, terminal = await _begin(recorder)
    now = datetime.now(UTC)
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(LLMUsageAttempt)
            .where(LLMUsageAttempt.id == stale.attempt_id)
            .values(started_at=now - timedelta(hours=2))
        )
        await session.execute(
            update(LLMUsageAttempt)
            .where(LLMUsageAttempt.id == fresh.attempt_id)
            .values(started_at=now)
        )
        await session.commit()
    assert await recorder.finalize(
        terminal,
        status="succeeded",
        actual_model="test-judge",
        input_tokens=1,
        output_tokens=1,
        latency_ms=1,
    )

    async with AsyncSessionLocal() as session:
        abandoned = await abandon_stale_attempts(
            session, older_than=now - timedelta(hours=1)
        )
        await session.commit()

    assert abandoned == [stale.attempt_id]
    stale_row = await _load_attempt(db_session, stale.attempt_id)
    assert stale_row.status == "abandoned"
    assert stale_row.finished_at is not None
    assert stale_row.error_code == "usage_finalization_missing"
    assert stale_row.input_tokens is None
    assert stale_row.output_tokens is None
    assert stale_row.estimated_cost_cny is None
    assert (await _load_attempt(db_session, fresh.attempt_id)).status == "pending"
    assert (await _load_attempt(db_session, terminal.attempt_id)).status == "succeeded"


async def test_unpersistable_paid_usage_never_raises_or_allows_replay(
    db_session, caplog: pytest.LogCaptureFixture
):
    expensive_prices = parse_price_book(
        '{"test-judge":{"input":999999999999.999999,'
        '"output":999999999999.999999}}'
    )
    recorder, handle = await _begin(UsageRecorder(prices=expensive_prices))

    with caplog.at_level(logging.CRITICAL):
        finalized = await recorder.finalize(
            handle,
            status="succeeded",
            actual_model="paid-provider-version",
            input_tokens=2_147_483_647,
            output_tokens=2_147_483_647,
            latency_ms=123,
        )

    assert finalized is False
    row = await _load_attempt(db_session, handle.attempt_id)
    assert row.status == "pending"
    assert row.finished_at is None
    critical = [record for record in caplog.records if record.levelno == logging.CRITICAL]
    assert len(critical) == 1
    record = critical[0]
    assert record.attempt_id == handle.attempt_id
    assert record.trace_id == "trace-1"
    assert record.retry_count == 0
    assert record.exception_class == "InvalidPriceBook"
    assert "999999" not in caplog.text


async def test_finalize_rejects_invalid_terminal_values_without_touching_pending(
    db_session, caplog: pytest.LogCaptureFixture
):
    recorder, handle = await _begin()
    invalid_cases = [
        {"status": "pending", "latency_ms": 1},
        {"status": "succeeded", "latency_ms": True},
        {"status": "succeeded", "latency_ms": 2_147_483_648},
        {"status": "succeeded", "latency_ms": 1, "input_tokens": -1},
    ]

    for case in invalid_cases:
        with caplog.at_level(logging.CRITICAL):
            assert not await recorder.finalize(handle, actual_model=None, **case)

    row = await _load_attempt(db_session, handle.attempt_id)
    assert row.status == "pending"
    assert row.finished_at is None
    assert len(
        [record for record in caplog.records if record.levelno == logging.CRITICAL]
    ) == len(invalid_cases)


async def test_gateway_primary_failure_and_fallback_are_two_content_free_rows(db_session):
    private_name = "Private Candidate Name"
    context = LLMCallContext(
        operation="judge",
        call_group_id=uuid4(),
        trace_id="usage-trace",
        jd_id=None,
    )
    gateway = LLMGateway(recorder=UsageRecorder(prices=_prices()))
    gateway._client.chat.completions.create = AsyncMock(
        side_effect=[
            APIConnectionError(
                request=httpx.Request(
                    "POST", "https://provider.invalid/v1/chat/completions"
                )
            ),
            SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok":true}'))],
                model="test-judge-fallback-versioned",
                usage=SimpleNamespace(prompt_tokens=9, completion_tokens=4),
            ),
        ]
    )

    response = await gateway.judge(
        {"resume_markdown": private_name}, schema={"type": "object"}, context=context
    )

    assert response.used_fallback is True
    rows = (
        await db_session.execute(
            select(LLMUsageAttempt)
            .where(LLMUsageAttempt.call_group_id == context.call_group_id)
            .order_by(LLMUsageAttempt.id)
        )
    ).scalars().all()
    assert len(rows) == 2
    assert [row.attempt_role for row in rows] == ["primary", "fallback"]
    assert [row.status for row in rows] == ["unavailable", "succeeded"]
    assert rows[0].error_code == "provider_unavailable"
    assert rows[1].error_code is None
    assert all(row.call_group_id == context.call_group_id for row in rows)
    assert private_name not in repr(
        [
            {column.name: getattr(row, column.name) for column in row.__table__.columns}
            for row in rows
        ]
    )
