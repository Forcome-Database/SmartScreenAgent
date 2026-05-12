from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.models.base import Base


class RuleVersion(Base):
    __tablename__ = "rule_versions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    jd_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("jds.id"), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    schema_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_by_user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"))
    notes: Mapped[str | None] = mapped_column(Text)
    golden_set_metrics: Mapped[dict | None] = mapped_column(JSONB)
