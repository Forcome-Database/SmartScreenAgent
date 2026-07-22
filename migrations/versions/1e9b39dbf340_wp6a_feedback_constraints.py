"""wp6a feedback constraints

Revision ID: 1e9b39dbf340
Revises: 2f27938b430b
Create Date: 2026-07-22 13:38:42.620084

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1e9b39dbf340'
down_revision: Union[str, Sequence[str], None] = '2f27938b430b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_unique_constraint(
        "uq_feedback_score_reviewer", "feedback", ["score_id", "reviewer_user_id"]
    )
    op.create_check_constraint(
        "ck_feedback_decision", "feedback", "decision IN ('advance', 'reject', 'hold')"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("ck_feedback_decision", "feedback", type_="check")
    op.drop_constraint("uq_feedback_score_reviewer", "feedback", type_="unique")
