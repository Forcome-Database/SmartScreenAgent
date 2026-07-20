from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.models.base import Base, TimestampMixin


class IngestionJob(Base, TimestampMixin):
    __tablename__ = "ingestion_jobs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    batch_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_external_id: Mapped[str | None] = mapped_column(String(128))
    jd_code: Mapped[str | None] = mapped_column(String(64))

    raw_file_key: Mapped[str] = mapped_column(String(256), nullable=False)
    raw_file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    raw_file_content_type: Mapped[str] = mapped_column(String(128), nullable=False)
    raw_file_original_name_cipher: Mapped[str] = mapped_column(Text, nullable=False)

    candidate_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("candidates.id"))
    score_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("scores.id"))

    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    trace_id: Mapped[str | None] = mapped_column(String(64))
    actor: Mapped[str] = mapped_column(String(64), nullable=False, default="system")

    __table_args__ = (
        Index("ix_ingestion_jobs_sha256", "raw_file_sha256"),
        Index("ix_ingestion_jobs_state_lease", "state", "lease_expires_at"),
        Index("ix_ingestion_jobs_batch", "batch_id"),
        Index(
            "uq_ingestion_jobs_sha256_active",
            "raw_file_sha256",
            unique=True,
            postgresql_where=text(
                "state NOT IN ('ready', 'completed', 'terminal_failed', 'deleted')"
            ),
        ),
        CheckConstraint("attempts >= 0", name="ck_ingestion_jobs_attempts_nonnegative"),
    )
