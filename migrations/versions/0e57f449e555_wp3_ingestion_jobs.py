"""WP3 ingestion jobs and score uniqueness.

Revision ID: 0e57f449e555
Revises: b57c2f9e1a6d
Create Date: 2026-07-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "0e57f449e555"
down_revision: str | Sequence[str] | None = "b57c2f9e1a6d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ingestion_jobs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("batch_id", UUID(as_uuid=True), nullable=True),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("source_external_id", sa.String(length=128), nullable=True),
        sa.Column("jd_code", sa.String(length=64), nullable=True),
        sa.Column("raw_file_key", sa.String(length=256), nullable=False),
        sa.Column("raw_file_sha256", sa.String(length=64), nullable=False),
        sa.Column("raw_file_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("raw_file_content_type", sa.String(length=128), nullable=False),
        sa.Column("raw_file_original_name_cipher", sa.Text(), nullable=False),
        sa.Column("candidate_id", sa.BigInteger(), sa.ForeignKey("candidates.id"), nullable=True),
        sa.Column("score_id", sa.BigInteger(), sa.ForeignKey("scores.id"), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("actor", sa.String(length=64), nullable=False, server_default="system"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_ingestion_jobs_sha256", "ingestion_jobs", ["raw_file_sha256"])
    op.create_index("ix_ingestion_jobs_state_lease", "ingestion_jobs", ["state", "lease_expires_at"])
    op.create_index("ix_ingestion_jobs_batch", "ingestion_jobs", ["batch_id"])
    op.create_check_constraint(
        "ck_ingestion_jobs_attempts_nonnegative", "ingestion_jobs", "attempts >= 0"
    )
    # Fails loudly if legacy duplicate scores exist; reconcile before deploy (see README rollout).
    op.create_unique_constraint(
        "uq_scores_candidate_jd_rule", "scores", ["candidate_id", "jd_id", "rule_version_id"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_scores_candidate_jd_rule", "scores", type_="unique")
    op.drop_index("ix_ingestion_jobs_batch", table_name="ingestion_jobs")
    op.drop_index("ix_ingestion_jobs_state_lease", table_name="ingestion_jobs")
    op.drop_index("ix_ingestion_jobs_sha256", table_name="ingestion_jobs")
    op.drop_constraint(
        "ck_ingestion_jobs_attempts_nonnegative", "ingestion_jobs", type_="check"
    )
    op.drop_table("ingestion_jobs")
