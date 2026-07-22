from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.models.base import Base, TimestampMixin


class Feedback(Base, TimestampMixin):
    __tablename__ = "feedback"

    __table_args__ = (
        UniqueConstraint("score_id", "reviewer_user_id", name="uq_feedback_score_reviewer"),
        CheckConstraint("decision IN ('advance', 'reject', 'hold')", name="ck_feedback_decision"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    score_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("scores.id"), nullable=False, index=True
    )
    reviewer_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=False
    )
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    ai_agreed: Mapped[bool | None] = mapped_column(Boolean)
