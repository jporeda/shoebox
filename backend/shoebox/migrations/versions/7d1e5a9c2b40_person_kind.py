"""person kind (person or pet)

Revision ID: 7d1e5a9c2b40
Revises: 355c464ff312
Create Date: 2026-11-09 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = '7d1e5a9c2b40'
down_revision: Union[str, None] = '355c464ff312'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Everyone already named is a person; pets are tagged by hand afterwards.
    with op.batch_alter_table('person', schema=None) as batch_op:
        batch_op.add_column(sa.Column('kind', sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default='person'))


def downgrade() -> None:
    with op.batch_alter_table('person', schema=None) as batch_op:
        batch_op.drop_column('kind')
