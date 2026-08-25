"""add auction match columns

Adds persisted hex-match results (target hex + delta E, in keys-only and
keys+dyes variants) to both auction tables so list/stats queries can filter,
sort and paginate in SQL.

Columns are added nullable and left empty; run
scripts/backfill_auction_matches.py after upgrading to populate existing rows.

Revision ID: a1b2c3d4e5f6
Revises: 74327adb4277
Create Date: 2026-08-25 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '74327adb4277'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


MATCH_COLUMNS = [
    ('target_hex_keys', sa.String(length=6)),
    ('delta_e_keys', sa.Float()),
    ('tier_keys', sa.String(length=2)),
    ('target_hex_all', sa.String(length=6)),
    ('delta_e_all', sa.Float()),
    ('tier_all', sa.String(length=2)),
]

INDEXES = {
    'active_auctions': [
        ('ix_active_tier_keys_price', ['tier_keys', 'price']),
        ('ix_active_tier_keys_end_time', ['tier_keys', 'end_time']),
        ('ix_active_tier_all_price', ['tier_all', 'price']),
        ('ix_active_tier_all_end_time', ['tier_all', 'end_time']),
        ('ix_active_target_keys_price', ['target_hex_keys', 'price']),
        ('ix_active_target_all_price', ['target_hex_all', 'price']),
    ],
    'ended_auctions': [
        ('ix_ended_tier_keys_price', ['tier_keys', 'price']),
        ('ix_ended_tier_keys_timestamp', ['tier_keys', 'timestamp']),
        ('ix_ended_tier_all_price', ['tier_all', 'price']),
        ('ix_ended_tier_all_timestamp', ['tier_all', 'timestamp']),
        ('ix_ended_target_keys_price', ['target_hex_keys', 'price']),
        ('ix_ended_target_all_price', ['target_hex_all', 'price']),
    ],
}


def upgrade() -> None:
    for table in INDEXES:
        for name, type_ in MATCH_COLUMNS:
            op.add_column(table, sa.Column(name, type_, nullable=True))
        for index_name, columns in INDEXES[table]:
            op.create_index(index_name, table, columns, unique=False)


def downgrade() -> None:
    for table in INDEXES:
        for index_name, _ in INDEXES[table]:
            op.drop_index(index_name, table_name=table)
        for name, _ in MATCH_COLUMNS:
            op.drop_column(table, name)
