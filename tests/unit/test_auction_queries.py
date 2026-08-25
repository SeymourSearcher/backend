import pytest
from sqlalchemy.dialects import mysql

from app.models import ActiveAuction, EndedAuction
from app.schemas.auction import EndedAuctionResponse
from app.services.auction_queries import (
    AuctionFilters,
    build_where,
    match_columns_for,
    parse_pieces,
    serialize,
    sort_column,
)


def sql(expr):
    return str(expr.compile(dialect=mysql.dialect(), compile_kwargs={"literal_binds": True}))


@pytest.mark.parametrize("raw, expected", [
    (None, None),
    ("", None),
    ("0,1,2,3", [0, 1, 2, 3]),
    ("2", [2]),
    (" 1 , 3 ", [1, 3]),
    ("a,b", None),
    ("1,x,3", [1, 3]),
    ("-1", None),
])
def test_parse_pieces(raw, expected):
    assert parse_pieces(raw) == expected


def test_match_columns_for_selects_the_right_column_pair():
    target, delta_e, tier = match_columns_for(EndedAuction, include_dyes=True)
    assert (target, delta_e, tier) == (
        EndedAuction.target_hex_all, EndedAuction.delta_e_all, EndedAuction.tier_all,
    )

    target, delta_e, tier = match_columns_for(EndedAuction, include_dyes=False)
    assert (target, delta_e, tier) == (
        EndedAuction.target_hex_keys, EndedAuction.delta_e_keys, EndedAuction.tier_keys,
    )


def test_build_where_no_filters_matches_everything():
    assert sql(build_where(EndedAuction, AuctionFilters())) == "true"


def test_build_where_normalises_hex():
    where = sql(build_where(EndedAuction, AuctionFilters(hex_color="#ff00aa")))
    assert "'FF00AA'" in where


def test_build_where_tier_is_an_equality_on_the_stored_column():
    where = sql(build_where(EndedAuction, AuctionFilters(tier="t2", include_dyes=True)))
    # An equality on tier_all is index-servable; a delta_e range would not be.
    assert "tier_all = 't2'" in where
    assert "delta_e" not in where


def test_build_where_tier_respects_include_dyes():
    where = sql(build_where(EndedAuction, AuctionFilters(tier="t1", include_dyes=False)))
    assert "tier_keys" in where and "tier_all" not in where


def test_build_where_unknown_tier_matches_nothing():
    assert sql(build_where(EndedAuction, AuctionFilters(tier="t9"))) == "false"


def test_build_where_unknown_set_matches_nothing():
    assert sql(build_where(EndedAuction, AuctionFilters(set_name="Nope"))) == "false"


def test_build_where_combines_filters():
    where = sql(build_where(EndedAuction, AuctionFilters(
        pieces=[0, 2], min_price=100, max_price=500, bin_filter="bin",
    )))
    assert "piece IN (0, 2)" in where
    assert "price >= 100" in where
    assert "price <= 500" in where
    assert "is_bin IS true" in where


def test_build_where_nobin():
    assert "is_bin IS false" in sql(build_where(EndedAuction, AuctionFilters(bin_filter="nobin")))


def test_sort_column_falls_back_per_model():
    assert sort_column(EndedAuction, "price") is EndedAuction.price
    assert sort_column(EndedAuction, "anything else") is EndedAuction.timestamp
    assert sort_column(ActiveAuction, "anything else") is ActiveAuction.end_time


def ended(**overrides):
    row = EndedAuction(
        auction_id="a1", seller_uuid="s", buyer_uuid="b", price=1000, is_bin=True,
        item_uuid="i", hex_color="FF0000", piece=0, timestamp=1_700_000_000_000,
        target_hex_keys="FF0102", delta_e_keys=1.23456,
        target_hex_all="FF0000", delta_e_all=0.0,
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


def test_serialize_uses_the_all_columns_when_dyes_included():
    data = serialize(ended(), EndedAuctionResponse, include_dyes=True)
    assert data.target_hex == "FF0000"
    assert data.delta_e == 0.0
    assert data.tier == "t1"
    assert data.abs_diff == 0


def test_serialize_uses_the_keys_columns_when_dyes_excluded():
    data = serialize(ended(), EndedAuctionResponse, include_dyes=False)
    assert data.target_hex == "FF0102"
    assert data.delta_e == 1.23  # rounded to 2dp for display
    assert data.tier == "t1"
    assert data.abs_diff == 3


def test_serialize_unmatched_row_does_not_raise():
    row = ended(target_hex_all=None, delta_e_all=None)
    data = serialize(row, EndedAuctionResponse, include_dyes=True)
    assert data.target_hex == ""
    assert data.delta_e == 0.0
    assert data.tier is None
    assert data.abs_diff == 0
