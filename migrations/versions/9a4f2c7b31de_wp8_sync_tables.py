"""wp8 sync tables

Revision ID: 9a4f2c7b31de
Revises: 7d3c9b1a4e62
Create Date: 2026-07-27 01:39:51.545208

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9a4f2c7b31de"
down_revision: str | Sequence[str] | None = "7d3c9b1a4e62"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "sync_cursors",
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("cursor_value", sa.String(length=64), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("source"),
    )
    op.create_table(
        "sync_source_items",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("source_external_id", sa.String(length=128), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("ingestion_job_id", sa.BigInteger(), nullable=True),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "outcome IN ('ingested','skipped_duplicate','failed')",
            name="ck_sync_source_items_outcome",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_sync_source_items_attempts"),
        sa.ForeignKeyConstraint(["ingestion_job_id"], ["ingestion_jobs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source",
            "source_external_id",
            "content_sha256",
            name="uq_sync_source_items_identity",
        ),
    )
    op.create_index(
        "ix_sync_source_items_outcome_attempts",
        "sync_source_items",
        ["outcome", "attempts"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("sync_source_items")
    op.drop_table("sync_cursors")
