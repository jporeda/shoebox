"""photo t_months: a date limited to some months (summer: Jun-Aug), not known which

Revision ID: f1a3c5e7b9d2
Revises: e4c8a1f7b2d9
Create Date: 2026-11-16 13:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f1a3c5e7b9d2'
down_revision: Union[str, None] = 'e4c8a1f7b2d9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('photo', schema=None) as batch_op:
        batch_op.add_column(sa.Column('t_months', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('photo', schema=None) as batch_op:
        batch_op.drop_column('t_months')
