"""Expiry is derived at read time rather than persisted, so these three helpers
are the whole contract: a lapsed order must read as EXPIRED everywhere without
any endpoint writing to it.
"""
from datetime import timedelta

import pytest
from sqlalchemy.dialects import mysql

from app.clock import utcnow
from app.models import BuyOrder, OrderStatus, SellOrder
from app.routers.marketplace import is_expired, status_filter, with_derived_status
from app.schemas.marketplace import BuyOrderResponse


class FakeOrder:
    def __init__(self, status, expires_at):
        self.status = status
        self.expires_at = expires_at


def order(status, hours):
    return FakeOrder(status, utcnow() + timedelta(hours=hours))


@pytest.mark.parametrize("status, hours, expected", [
    (OrderStatus.ACTIVE, 5, False),
    (OrderStatus.ACTIVE, -1, True),
    (OrderStatus.CANCELLED, -1, False),
    (OrderStatus.EXPIRED, -1, False),
    (OrderStatus.CANCELLED, 5, False),
])
def test_is_expired(status, hours, expected):
    assert is_expired(order(status, hours)) is expected


def sql(expr):
    return str(expr.compile(dialect=mysql.dialect(), compile_kwargs={"literal_binds": True}))


def test_status_filter_none_is_no_filter():
    assert status_filter(BuyOrder, None) is None


@pytest.mark.parametrize("model", [BuyOrder, SellOrder])
def test_status_filter_active_excludes_lapsed_rows(model):
    where = sql(status_filter(model, OrderStatus.ACTIVE))
    assert "status = 'ACTIVE'" in where
    assert "expires_at >" in where


@pytest.mark.parametrize("model", [BuyOrder, SellOrder])
def test_status_filter_expired_includes_lapsed_active_rows(model):
    where = sql(status_filter(model, OrderStatus.EXPIRED))
    assert "OR" in where
    assert "status = 'EXPIRED'" in where
    assert "status = 'ACTIVE'" in where
    assert "expires_at <=" in where


def test_status_filter_other_statuses_are_plain_equality():
    where = sql(status_filter(BuyOrder, OrderStatus.CANCELLED))
    assert "expires_at" not in where
    assert "status = 'CANCELLED'" in where


def response(status):
    return BuyOrderResponse.model_construct(status=status)


def test_with_derived_status_overrides_only_lapsed_active():
    assert with_derived_status(
        response(OrderStatus.ACTIVE), order(OrderStatus.ACTIVE, -1)
    ).status == OrderStatus.EXPIRED

    assert with_derived_status(
        response(OrderStatus.ACTIVE), order(OrderStatus.ACTIVE, 5)
    ).status == OrderStatus.ACTIVE

    assert with_derived_status(
        response(OrderStatus.CANCELLED), order(OrderStatus.CANCELLED, -1)
    ).status == OrderStatus.CANCELLED


def test_with_derived_status_does_not_mutate_the_orm_row():
    row = order(OrderStatus.ACTIVE, -1)
    with_derived_status(response(OrderStatus.ACTIVE), row)
    assert row.status == OrderStatus.ACTIVE
