"""photo location: a general area, exported as a keyword

Revision ID: e4c8a1f7b2d9
Revises: d7a2c4e81b06
Create Date: 2026-11-16 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = 'e4c8a1f7b2d9'
down_revision: Union[str, None] = 'd7a2c4e81b06'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('photo', schema=None) as batch_op:
        batch_op.add_column(sa.Column('location', sqlmodel.sql.sqltypes.AutoString(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('photo', schema=None) as batch_op:
        batch_op.drop_column('location')
