"""create users and seymours

Revision ID: b7e1c0a5d3f2
Revises:
Create Date: 2026-08-25 00:00:00.000000

Backfills the base of the chain: `users` and `seymours` were originally created
by Base.metadata.create_all rather than a migration, so every later revision
that references them (the buy_orders FK, sell_orders FK, ix_seymours_hex_color)
could only run against a database built out of band.

Deliberately does not create ix_seymours_hex_color -- 74327adb4277 adds it.
"""
from typing import Sequence, Union

from alembic import context, op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7e1c0a5d3f2'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(name: str) -> bool:
    if context.is_offline_mode():
        return False
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    # Databases predating this revision already have both tables from
    # create_all; skip so they can upgrade through the chain unchanged.
    if not _has_table('users'):
        op.create_table('users',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('discord_id', sa.BigInteger(), nullable=False),
        sa.Column('username', sa.String(length=255), nullable=False),
        sa.Column('avatar_hash', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('last_uploaded', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_users_discord_id'), 'users', ['discord_id'], unique=True)

    if not _has_table('seymours'):
        op.create_table('seymours',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('item_name', sa.SmallInteger(), nullable=False),
        sa.Column('hex_color', sa.String(length=6), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
        )
        op.create_index(op.f('ix_seymours_user_id'), 'seymours', ['user_id'], unique=False)
        op.create_index('ix_seymours_user_item', 'seymours', ['user_id', 'item_name'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_seymours_user_item', table_name='seymours')
    op.drop_index(op.f('ix_seymours_user_id'), table_name='seymours')
    op.drop_table('seymours')
    op.drop_index(op.f('ix_users_discord_id'), table_name='users')
    op.drop_table('users')
