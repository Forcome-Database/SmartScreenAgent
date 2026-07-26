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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.models.base import Base


class GoldenSetSnapshot(Base):
    __tablename__ = "golden_set_snapshots"

    __table_args__ = (
        UniqueConstraint(
            "content_sha256", name="uq_golden_snapshots_content_sha256"
        ),
        CheckConstraint("item_count >= 0", name="ck_golden_snapshot_item_count"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    item_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GoldenSetSnapshotEntry(Base):
    __tablename__ = "golden_set_snapshot_entries"

    __table_args__ = (
        UniqueConstraint(
            "snapshot_id",
            "candidate_id",
            "jd_id",
            name="uq_golden_snapshot_candidate_jd",
        ),
        CheckConstraint(
            "label IN ('advance', 'reject', 'borderline')",
            name="ck_golden_snapshot_label",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("golden_set_snapshots.id", ondelete="CASCADE"),
        nullable=False,
    )
    candidate_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("candidates.id"), nullable=False
    )
    jd_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("jds.id"), nullable=False)
    label: Mapped[str] = mapped_column(String(32), nullable=False)


class QualityRelease(Base):
    __tablename__ = "quality_releases"

    __table_args__ = (
        CheckConstraint(
            "status IN ('meets_target', 'below_target')",
            name="ck_quality_release_status",
        ),
        Index("ix_quality_release_created_id", "created_at", "id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    golden_snapshot_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("golden_set_snapshots.id"), nullable=False
    )
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    metrics_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    targets_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_by_user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class QualityReleaseJD(Base):
    __tablename__ = "quality_release_jds"

    __table_args__ = (
        UniqueConstraint(
            "quality_release_id", "jd_id", name="uq_quality_release_jd"
        ),
        Index("ix_quality_release_jds_jd_release", "jd_id", "quality_release_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    quality_release_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("quality_releases.id", ondelete="CASCADE"),
        nullable=False,
    )
    jd_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("jds.id"), nullable=False)
    rule_version_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("rule_versions.id"), nullable=False
    )
    metrics_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
