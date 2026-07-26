"""wp6c rule version status

Revision ID: 25954dc70368
Revises: f412481450cf
Create Date: 2026-07-23 00:23:35.542884

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "25954dc70368"
down_revision: str | Sequence[str] | None = "f412481450cf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "rule_versions",
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="published",
        ),
    )
    op.create_check_constraint(
        "ck_rule_versions_status",
        "rule_versions",
        "status IN ('draft', 'published', 'archived')",
    )
    op.create_unique_constraint(
        "uq_rule_versions_jd_version",
        "rule_versions",
        ["jd_id", "version"],
    )
    op.alter_column(
        "rule_versions",
        "published_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=True,
    )


def downgrade() -> None:
    """Downgrade schema."""
    # Draft rows have no meaningful published_at; drop them before restoring NOT NULL.
    op.execute("DELETE FROM rule_versions WHERE status = 'draft'")
    op.alter_column(
        "rule_versions",
        "published_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
    )
    op.drop_constraint(
        "uq_rule_versions_jd_version",
        "rule_versions",
        type_="unique",
    )
    op.drop_constraint(
        "ck_rule_versions_status",
        "rule_versions",
        type_="check",
    )
    op.drop_column("rule_versions", "status")
