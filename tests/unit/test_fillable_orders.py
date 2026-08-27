"""Fillable orders load the order list before the seymour list.

Matching is O(orders x seymours) in Python, and the marketplace is usually empty,
so querying seymours first spent the entire request building rows to match
against nothing -- 250ms for a user with 17.5k of them. These tests pin the
order, since the two queries are independent and swapping them back is invisible.
"""
from types import SimpleNamespace

import pytest

from app.services import buy_orders


class FakeSeymourRepo:
    def __init__(self, session):
        self.session = session

    async def list_for_user(self, user_id):
        session.calls.append("seymours")
        return session.seymours


class FakeBuyOrderRepo:
    def __init__(self, session):
        self.session = session

    async def list_active_excluding(self, user_id):
        session.calls.append("orders")
        return session.orders


session = SimpleNamespace(calls=[], seymours=[], orders=[])


@pytest.fixture(autouse=True)
def fake_repos(monkeypatch):
    session.calls, session.seymours, session.orders = [], [], []
    monkeypatch.setattr(buy_orders, "SeymourRepository", FakeSeymourRepo)
    monkeypatch.setattr(buy_orders, "BuyOrderRepository", FakeBuyOrderRepo)
    # Criteria matching is covered by the marketplace helpers; these tests are
    # about which queries run and how the results are ordered.
    monkeypatch.setattr(buy_orders, "_matches", lambda s, o: s.hex_color == o.want)


def seymour(id, hex_color):
    return SimpleNamespace(id=id, hex_color=hex_color)


def order(id, want, price, expires_at):
    return SimpleNamespace(id=id, want=want, price=price, expires_at=expires_at)


user = SimpleNamespace(id=1)


async def fillable(sort_by="price", sort_order="desc", skip=0, limit=50):
    return await buy_orders.list_fillable(None, user, sort_by, sort_order, skip, limit)


@pytest.mark.asyncio
async def test_no_orders_never_queries_seymours():
    session.seymours = [seymour(1, "FFFFFF")]
    assert await fillable() == []
    assert session.calls == ["orders"]


@pytest.mark.asyncio
async def test_no_seymours_returns_nothing():
    session.orders = [order(1, "FFFFFF", 10, 5)]
    assert await fillable() == []


@pytest.mark.asyncio
async def test_only_orders_with_a_match_are_returned():
    session.seymours = [seymour(1, "FFFFFF"), seymour(2, "000000"), seymour(3, "FFFFFF")]
    session.orders = [order(1, "FFFFFF", 10, 5), order(2, "ABCDEF", 99, 1)]

    result = await fillable()
    assert [r["order"].id for r in result] == [1]
    assert [s.id for s in result[0]["matching_seymours"]] == [1, 3]


@pytest.mark.parametrize("sort_by, sort_order, expected", [
    ("price", "desc", [2, 1]),
    ("price", "asc", [1, 2]),
    ("expires_at", "desc", [1, 2]),
    ("expires_at", "asc", [2, 1]),
])
@pytest.mark.asyncio
async def test_sorting(sort_by, sort_order, expected):
    session.seymours = [seymour(1, "FFFFFF")]
    session.orders = [order(1, "FFFFFF", 10, 50), order(2, "FFFFFF", 99, 5)]

    assert [r["order"].id for r in await fillable(sort_by, sort_order)] == expected


@pytest.mark.asyncio
async def test_pagination_applies_after_sorting():
    session.seymours = [seymour(1, "FFFFFF")]
    session.orders = [order(i, "FFFFFF", i, i) for i in range(1, 6)]

    assert [r["order"].id for r in await fillable(skip=1, limit=2)] == [4, 3]
