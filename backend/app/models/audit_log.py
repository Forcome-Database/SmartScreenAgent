from sqlalchemy import BigInteger, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from backend.app.models.base import Base, TimestampMixin


class AuditLog(Base, TimestampMixin):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(64))
    target_id: Mapped[int | None] = mapped_column(BigInteger)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    rule_version_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("rule_versions.id"))

    __table_args__ = (
        Index("ix_audit_event_created", "event_type", "created_at"),
    )
