"""add_players_last_updated_index

Revision ID: c4f9a2b81d7e
Revises: a1b2c3d4e5f6
Create Date: 2026-08-26 13:40:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'c4f9a2b81d7e'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index('ix_players_last_updated', 'players', ['last_updated'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_players_last_updated', table_name='players')
