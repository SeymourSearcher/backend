"""create user_stats

Per-user Seymour tallies, recomputed on write instead of being derived on read
and cached in Redis. Backfill for existing users:
scripts/backfill_user_stats.py.

Revision ID: a8c1f4e60b92
Revises: e7d31f5a92c4
Create Date: 2026-08-26 19:20:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a8c1f4e60b92'
down_revision: Union[str, None] = 'e7d31f5a92c4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

STAT_COLUMNS = (
    "stars", "t1", "t2", "t3", "words", "dupes",
    "total_sets", "dyed_sets", "total_seymours",
)


def upgrade() -> None:
    op.create_table(
        'user_stats',
        sa.Column('user_id', sa.Integer(), nullable=False),
        *[sa.Column(name, sa.Integer(), nullable=False, server_default='0') for name in STAT_COLUMNS],
        sa.Column('computed_at', sa.DateTime(), nullable=False,
                  server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('user_id'),
    )
    # The leaderboard's only ordering.
    op.create_index(
        'ix_user_stats_total_seymours', 'user_stats', ['total_seymours'], unique=False
    )


def downgrade() -> None:
    op.drop_index('ix_user_stats_total_seymours', table_name='user_stats')
    op.drop_table('user_stats')
