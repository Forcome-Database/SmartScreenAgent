from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.models.base import Base


class LLMUsageAttempt(Base):
    __tablename__ = "llm_usage_attempts"

    __table_args__ = (
        CheckConstraint(
            "operation IN ('extract', 'judge', 'cross_check', 'lightweight')",
            name="ck_llm_usage_operation",
        ),
        CheckConstraint(
            "attempt_role IN ('primary', 'fallback', 'secondary')",
            name="ck_llm_usage_attempt_role",
        ),
        CheckConstraint(
            "status IN ('pending', 'succeeded', 'unavailable', 'invalid_response', "
            "'configuration_error', 'abandoned')",
            name="ck_llm_usage_status",
        ),
        CheckConstraint(
            "(status = 'pending') = (finished_at IS NULL)",
            name="ck_llm_usage_terminal_time",
        ),
        CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0",
            name="ck_llm_usage_input_tokens",
        ),
        CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0",
            name="ck_llm_usage_output_tokens",
        ),
        CheckConstraint(
            "input_price_cny_per_million >= 0 AND output_price_cny_per_million >= 0",
            name="ck_llm_usage_prices",
        ),
        CheckConstraint(
            "estimated_cost_cny IS NULL OR estimated_cost_cny >= 0",
            name="ck_llm_usage_cost",
        ),
        CheckConstraint(
            "latency_ms IS NULL OR latency_ms >= 0",
            name="ck_llm_usage_latency",
        ),
        Index("ix_llm_usage_started_id", "started_at", "id"),
        Index("ix_llm_usage_status_started", "status", "started_at"),
        Index("ix_llm_usage_jd_rule_started", "jd_id", "rule_version_id", "started_at"),
        Index("ix_llm_usage_operation_started", "operation", "started_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    call_group_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), nullable=False, index=True
    )
    trace_id: Mapped[str | None] = mapped_column(String(64), index=True)
    ingestion_job_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("ingestion_jobs.id"), index=True
    )
    score_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("scores.id"), index=True)
    jd_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("jds.id"), index=True)
    rule_version_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("rule_versions.id"), index=True
    )
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt_role: Mapped[str] = mapped_column(String(16), nullable=False)
    requested_model: Mapped[str] = mapped_column(String(128), nullable=False)
    actual_model: Mapped[str | None] = mapped_column(String(128))
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    input_price_cny_per_million: Mapped[Decimal] = mapped_column(
        Numeric(18, 6), nullable=False
    )
    output_price_cny_per_million: Mapped[Decimal] = mapped_column(
        Numeric(18, 6), nullable=False
    )
    estimated_cost_cny: Mapped[Decimal | None] = mapped_column(Numeric(24, 12))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OperationsReconciliationState(Base):
    __tablename__ = "operations_reconciliation_state"

    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    next_period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
