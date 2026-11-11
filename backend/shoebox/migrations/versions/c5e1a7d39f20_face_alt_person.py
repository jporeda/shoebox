"""face alt person: "one of two people" (twins)

Revision ID: c5e1a7d39f20
Revises: 4e2a7c9b1d58
Create Date: 2026-11-11 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c5e1a7d39f20'
down_revision: Union[str, None] = '4e2a7c9b1d58'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('face', schema=None) as batch_op:
        batch_op.add_column(sa.Column('alt_person_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_face_alt_person_id'), ['alt_person_id'], unique=False)
        batch_op.create_foreign_key('fk_face_alt_person_id_person', 'person', ['alt_person_id'], ['id'])


def downgrade() -> None:
    with op.batch_alter_table('face', schema=None) as batch_op:
        batch_op.drop_constraint('fk_face_alt_person_id_person', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_face_alt_person_id'))
        batch_op.drop_column('alt_person_id')
