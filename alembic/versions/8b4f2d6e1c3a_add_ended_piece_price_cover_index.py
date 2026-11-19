"""cover the per-piece auction stats with one ended_auctions index

Every per-piece stats pass (scalars, percentile and histogram ROW_NUMBER over
price, hourly counts) reads only these columns, so they run index-only and the
ROW_NUMBER passes read one piece already in price order. ix_ended_auctions_piece
is a strict prefix of it (and of ix_ended_piece_timestamp), so it goes.

Revision ID: 8b4f2d6e1c3a
Revises: 5d2e8f1c7a90
Create Date: 2026-11-19 21:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '8b4f2d6e1c3a'
down_revision: Union[str, None] = '5d2e8f1c7a90'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        'ix_ended_piece_price_cover', 'ended_auctions',
        ['piece', 'price', 'is_bin', 'tier_all', 'tier_keys', 'timestamp'],
        unique=False,
    )
    op.drop_index('ix_ended_auctions_piece', table_name='ended_auctions')


def downgrade() -> None:
    op.create_index('ix_ended_auctions_piece', 'ended_auctions', ['piece'], unique=False)
    op.drop_index('ix_ended_piece_price_cover', table_name='ended_auctions')
