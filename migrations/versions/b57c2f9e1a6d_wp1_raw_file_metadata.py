"""WP1 raw-file integrity metadata.

Revision ID: b57c2f9e1a6d
Revises: 3884ec28fea9
Create Date: 2026-07-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b57c2f9e1a6d"
down_revision: str | Sequence[str] | None = "3884ec28fea9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "candidates", sa.Column("raw_file_sha256", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "candidates", sa.Column("raw_file_size_bytes", sa.BigInteger(), nullable=True)
    )
    op.add_column(
        "candidates", sa.Column("raw_file_content_type", sa.String(length=128), nullable=True)
    )
    op.add_column(
        "candidates", sa.Column("raw_file_original_name_cipher", sa.Text(), nullable=True)
    )
    op.create_check_constraint(
        "ck_candidates_raw_file_sha256_length",
        "candidates",
        "raw_file_sha256 IS NULL OR char_length(raw_file_sha256) = 64",
    )
    op.create_check_constraint(
        "ck_candidates_raw_file_size_nonnegative",
        "candidates",
        "raw_file_size_bytes IS NULL OR raw_file_size_bytes >= 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_candidates_raw_file_size_nonnegative", "candidates", type_="check"
    )
    op.drop_constraint(
        "ck_candidates_raw_file_sha256_length", "candidates", type_="check"
    )
    op.drop_column("candidates", "raw_file_original_name_cipher")
    op.drop_column("candidates", "raw_file_content_type")
    op.drop_column("candidates", "raw_file_size_bytes")
    op.drop_column("candidates", "raw_file_sha256")
