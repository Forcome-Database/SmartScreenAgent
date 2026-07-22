"""wp6b golden set label check

Revision ID: f412481450cf
Revises: 1e9b39dbf340
Create Date: 2026-07-22 20:38:01.039205

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f412481450cf'
down_revision: Union[str, Sequence[str], None] = '1e9b39dbf340'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_check_constraint(
        "ck_golden_set_label", "golden_set", "label IN ('advance', 'reject', 'borderline')"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("ck_golden_set_label", "golden_set", type_="check")
