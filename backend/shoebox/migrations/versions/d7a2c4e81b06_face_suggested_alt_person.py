"""face suggested alt person: "Ana or Ben?" suggestions for twins

Revision ID: d7a2c4e81b06
Revises: c5e1a7d39f20
Create Date: 2026-11-11 18:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd7a2c4e81b06'
down_revision: Union[str, None] = 'c5e1a7d39f20'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('face', schema=None) as batch_op:
        batch_op.add_column(sa.Column('suggested_alt_person_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_face_suggested_alt_person_id'), ['suggested_alt_person_id'], unique=False)
        batch_op.create_foreign_key('fk_face_suggested_alt_person_id_person', 'person', ['suggested_alt_person_id'], ['id'])


def downgrade() -> None:
    with op.batch_alter_table('face', schema=None) as batch_op:
        batch_op.drop_constraint('fk_face_suggested_alt_person_id_person', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_face_suggested_alt_person_id'))
        batch_op.drop_column('suggested_alt_person_id')
