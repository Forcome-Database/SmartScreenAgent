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
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.models.base import Base, TimestampMixin


class ScoreCrossCheck(Base, TimestampMixin):
    __tablename__ = "score_cross_checks"

    __table_args__ = (
        UniqueConstraint(
            "score_id",
            "secondary_model",
            "prompt_version",
            name="uq_cross_checks_score_model_prompt",
        ),
        CheckConstraint(
            "state IN ('queued', 'running', 'completed', 'retryable_failed', "
            "'terminal_failed')",
            name="ck_cross_checks_state",
        ),
        CheckConstraint("attempts >= 0", name="ck_cross_checks_attempts"),
        Index("ix_cross_checks_state_lease", "state", "lease_expires_at"),
        Index("ix_cross_checks_score_id_id", "score_id", "id"),
        Index("ix_cross_checks_completed_diff", "completed_at", "absolute_diff"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    score_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("scores.id"), nullable=False)
    secondary_model: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    sample_reasons: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    secondary_total_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    secondary_dimensions: Mapped[list[dict] | None] = mapped_column(JSONB)
    absolute_diff: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    threshold_snapshot: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
