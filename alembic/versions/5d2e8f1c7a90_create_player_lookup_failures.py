"""create player_lookup_failures

Revision ID: 5d2e8f1c7a90
Revises: b3e7c9a15d84
Create Date: 2026-11-19 14:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '5d2e8f1c7a90'
down_revision: Union[str, None] = 'b3e7c9a15d84'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'player_lookup_failures',
        sa.Column('uuid', sa.String(length=32), nullable=False),
        sa.Column('failed_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('uuid'),
    )


def downgrade() -> None:
    op.drop_table('player_lookup_failures')
