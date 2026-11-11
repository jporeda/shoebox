"""photo not_dup_of: pairs someone marked as not duplicates

Revision ID: b8d2f4a61c03
Revises: 4e2a7c9b1d58
Create Date: 2026-11-11 10:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b8d2f4a61c03'
down_revision: Union[str, None] = '4e2a7c9b1d58'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('photo', schema=None) as batch_op:
        batch_op.add_column(sa.Column('not_dup_of', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('photo', schema=None) as batch_op:
        batch_op.drop_column('not_dup_of')
