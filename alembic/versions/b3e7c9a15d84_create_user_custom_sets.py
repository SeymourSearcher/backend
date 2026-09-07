"""Create user_custom_sets

Backs the homepage featured-sets gallery. Existing users have no rows until they
upload again, so run scripts/backfill_custom_sets.py after upgrading.

Revision ID: b3e7c9a15d84
Revises: a8c1f4e60b92
Create Date: 2026-09-07 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3e7c9a15d84'
down_revision: Union[str, None] = 'a8c1f4e60b92'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'user_custom_sets',
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('set_rank', sa.SmallInteger(), nullable=False),
        sa.Column('avg_delta_e', sa.Float(), nullable=False),
        sa.Column('helmet_hex', sa.String(length=6), nullable=False),
        sa.Column('chestplate_hex', sa.String(length=6), nullable=False),
        sa.Column('leggings_hex', sa.String(length=6), nullable=False),
        sa.Column('boots_hex', sa.String(length=6), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('user_id', 'set_rank'),
    )
    op.create_index(
        'ix_user_custom_sets_rank_delta',
        'user_custom_sets',
        ['set_rank', 'avg_delta_e'],
    )


def downgrade() -> None:
    op.drop_index('ix_user_custom_sets_rank_delta', table_name='user_custom_sets')
    op.drop_table('user_custom_sets')
