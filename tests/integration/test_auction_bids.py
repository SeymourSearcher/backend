"""GET /auctions/recent-bids.

Had no coverage before the repository refactor moved its query and assembly out
of the router, so nothing would have caught the move going wrong.
"""
import pytest

from app.models import ActiveAuction
from app.models.auction import Bid
from app.services.matching import get_tier

pytestmark = pytest.mark.integration

BASE_MS = 1_700_000_000_000


@pytest.fixture(autouse=True)
def no_upstream_lookups(monkeypatch):
    async def fake(db, uuids):
        return {uuid: f"player-{uuid[:4]}" for uuid in uuids}

    monkeypatch.setattr("app.services.auction_queries.get_player_names", fake)


@pytest.fixture
async def bids(db):
    """Three auctions, one bid each, bid timestamps ascending with the index."""
    for i in range(3):
        delta_e = i * 1.5
        db.add(ActiveAuction(
            auction_id=f"a{i}", seller_uuid=f"s{i:03d}", price=100 + i, is_bin=False,
            start_time=BASE_MS, end_time=BASE_MS + 10_000, item_uuid=f"i{i}",
            hex_color="FF0000", piece=i,
            target_hex_keys="FF0102", delta_e_keys=delta_e, tier_keys=get_tier(delta_e),
            target_hex_all="FF0000", delta_e_all=delta_e + 0.5,
            tier_all=get_tier(delta_e + 0.5),
        ))
    await db.flush()
    for i in range(3):
        db.add(Bid(auction_id=f"a{i}", bidder_uuid=f"b{i:03d}", amount=500 + i,
                   timestamp=BASE_MS + i * 1000))
    await db.commit()


async def test_returns_newest_bids_first_with_resolved_names(client, bids):
    body = (await client.get("/auctions/recent-bids")).json()

    assert [b["auction_id"] for b in body] == ["a2", "a1", "a0"]
    assert body[0]["amount"] == 502
    assert body[0]["bidder_name"] == "player-b002"


async def test_limit_caps_the_result(client, bids):
    body = (await client.get("/auctions/recent-bids", params={"limit": 2})).json()

    assert [b["auction_id"] for b in body] == ["a2", "a1"]


async def test_include_dyes_switches_which_match_columns_are_reported(client, bids):
    with_dyes = (await client.get("/auctions/recent-bids", params={"include_dyes": True})).json()
    without = (await client.get("/auctions/recent-bids", params={"include_dyes": False})).json()

    assert with_dyes[0]["target_hex"] == "FF0000"
    assert without[0]["target_hex"] == "FF0102"
    assert with_dyes[0]["delta_e"] != without[0]["delta_e"]
