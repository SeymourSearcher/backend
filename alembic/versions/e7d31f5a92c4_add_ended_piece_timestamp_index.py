"""add ended_auctions (piece, timestamp) index

Revision ID: e7d31f5a92c4
Revises: c4f9a2b81d7e
Create Date: 2026-08-26 16:05:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'e7d31f5a92c4'
down_revision: Union[str, None] = 'c4f9a2b81d7e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        'ix_ended_piece_timestamp', 'ended_auctions', ['piece', 'timestamp'], unique=False
    )


def downgrade() -> None:
    op.drop_index('ix_ended_piece_timestamp', table_name='ended_auctions')
