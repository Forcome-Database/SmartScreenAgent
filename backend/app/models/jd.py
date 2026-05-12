from sqlalchemy import BigInteger, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.models.base import Base, TimestampMixin


class JD(Base, TimestampMixin):
    __tablename__ = "jds"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    active_rule_version_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("rule_versions.id", use_alter=True, name="fk_jd_active_rule")
    )
