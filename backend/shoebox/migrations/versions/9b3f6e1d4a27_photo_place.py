"""photo place: country, state, city

Revision ID: 9b3f6e1d4a27
Revises: 7d1e5a9c2b40
Create Date: 2026-11-09 16:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = '9b3f6e1d4a27'
down_revision: Union[str, None] = '7d1e5a9c2b40'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PLACE = ('country', 'state', 'city')


def upgrade() -> None:
    with op.batch_alter_table('photo', schema=None) as batch_op:
        for name in PLACE:
            batch_op.add_column(sa.Column(name, sqlmodel.sql.sqltypes.AutoString(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('photo', schema=None) as batch_op:
        for name in reversed(PLACE):
            batch_op.drop_column(name)
