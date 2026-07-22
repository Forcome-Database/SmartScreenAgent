from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.models.base import Base


class RuleVersion(Base):
    __tablename__ = "rule_versions"

    __table_args__ = (
        UniqueConstraint("jd_id", "version", name="uq_rule_versions_jd_version"),
        CheckConstraint(
            "status IN ('draft', 'published', 'archived')",
            name="ck_rule_versions_status",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    jd_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("jds.id"), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    schema_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="published")
    # Drafts are introduced with the publication service in Task 3; keep the
    # pre-existing non-null type until its read serializers become draft-aware.
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    published_by_user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"))
    notes: Mapped[str | None] = mapped_column(Text)
    golden_set_metrics: Mapped[dict | None] = mapped_column(JSONB)
