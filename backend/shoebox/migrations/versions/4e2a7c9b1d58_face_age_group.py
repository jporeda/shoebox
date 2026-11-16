"""face age group (FairFace)

Revision ID: 4e2a7c9b1d58
Revises: 9b3f6e1d4a27
Create Date: 2026-11-09 22:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '4e2a7c9b1d58'
down_revision: Union[str, None] = '9b3f6e1d4a27'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Existing faces get age_ver 0, so the next faces job estimates their age again with FairFace.
    with op.batch_alter_table('face', schema=None) as batch_op:
        batch_op.add_column(sa.Column('age_group', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('age_ver', sa.Integer(), nullable=False, server_default='0'))


def downgrade() -> None:
    with op.batch_alter_table('face', schema=None) as batch_op:
        batch_op.drop_column('age_ver')
        batch_op.drop_column('age_group')
