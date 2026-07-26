from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.models.base import Base


class SyncCursor(Base):
    """Where the last successful pull for one source stopped."""

    __tablename__ = "sync_cursors"

    source: Mapped[str] = mapped_column(String(64), primary_key=True)
    cursor_value: Mapped[str] = mapped_column(String(64), nullable=False)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SyncSourceItem(Base):
    """One (source, external id, content) triple we have already seen.

    The content hash is part of the identity on purpose: a candidate who
    uploads a revised resume must be ingested again, while the same bytes must
    never be downloaded or parsed twice.
    """

    __tablename__ = "sync_source_items"
    __table_args__ = (
        UniqueConstraint(
            "source",
            "source_external_id",
            "content_sha256",
            name="uq_sync_source_items_identity",
        ),
        CheckConstraint(
            "outcome IN ('ingested','skipped_duplicate','failed')",
            name="ck_sync_source_items_outcome",
        ),
        CheckConstraint("attempts >= 0", name="ck_sync_source_items_attempts"),
        Index("ix_sync_source_items_outcome_attempts", "outcome", "attempts"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    ingestion_job_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("ingestion_jobs.id")
    )
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
