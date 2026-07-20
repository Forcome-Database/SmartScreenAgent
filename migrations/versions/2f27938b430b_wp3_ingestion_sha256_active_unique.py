"""WP3 ingestion jobs sha256 active-state unique index.

Revision ID: 2f27938b430b
Revises: 0e57f449e555
Create Date: 2026-07-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2f27938b430b"
down_revision: str | Sequence[str] | None = "0e57f449e555"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "uq_ingestion_jobs_sha256_active",
        "ingestion_jobs",
        ["raw_file_sha256"],
        unique=True,
        postgresql_where=sa.text(
            "state NOT IN ('ready', 'completed', 'terminal_failed', 'deleted')"
        ),
    )


def downgrade() -> None:
    op.drop_index("uq_ingestion_jobs_sha256_active", table_name="ingestion_jobs")
