from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.models.base import Base, TimestampMixin


class Score(Base, TimestampMixin):
    __tablename__ = "scores"

    __table_args__ = (
        UniqueConstraint(
            "candidate_id", "jd_id", "rule_version_id", name="uq_scores_candidate_jd_rule"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    candidate_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("candidates.id"), nullable=False, index=True
    )
    jd_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("jds.id"), nullable=False, index=True)
    rule_version_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("rule_versions.id"), nullable=False
    )

    total_score: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)
    grade: Mapped[str] = mapped_column(String(16), nullable=False)
    hard_filter_result: Mapped[dict] = mapped_column(JSONB, nullable=False)
    rule_dimensions: Mapped[dict] = mapped_column(JSONB, nullable=False)
    judge_dimensions: Mapped[dict | None] = mapped_column(JSONB)
    cross_engine_diff: Mapped[float | None] = mapped_column(Numeric(6, 2))
    is_suspicious: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    llm_model_main: Mapped[str | None] = mapped_column(String(64))
    llm_model_extract: Mapped[str | None] = mapped_column(String(64))
    cost_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_cny: Mapped[float] = mapped_column(Numeric(10, 4), default=0, nullable=False)
