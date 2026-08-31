from datetime import date
from decimal import Decimal

import pytest

from src.backend.repositories.order_repository import (
    FixtureOrderRepository,
    Order,
    OrderNotFoundError,
    get_order_repository,
)


@pytest.fixture
def repo():
    return FixtureOrderRepository()


def test_get_by_id_returns_known_fixture_order(repo):
    order = repo.get_by_id("ORD2026080001")
    assert isinstance(order, Order)
    assert order.customer_id == "CUS0001"
    assert isinstance(order.amount, Decimal)
    # legacy fixture orders.json has no order_status/refund_status columns
    assert order.order_status is None
    assert order.refund_status is None
    assert order.rounding_adjustment == Decimal("0")


def test_get_by_id_raises_not_found(repo):
    with pytest.raises(OrderNotFoundError):
        repo.get_by_id("ORD9999-DOES-NOT-EXIST")


def test_list_by_customer_id_returns_all_orders_newest_first(repo):
    orders = repo.list_by_customer_id("CUS0021")
    assert len(orders) == 4
    dates = [o.order_date for o in orders]
    assert dates == sorted(dates, reverse=True)


def test_get_order_items_returns_all_items_for_multi_item_order(repo):
    items = repo.get_order_items("ORD2026080027")
    assert len(items) == 2
    product_ids = {i.product_id for i in items}
    assert product_ids == {"PRO0001", "PRO0006"}
    assert all(isinstance(i.unit_price, Decimal) for i in items)
    assert all(isinstance(i.paid_amount, Decimal) for i in items)


def test_get_order_with_items_combines_order_and_items(repo):
    combo = repo.get_order_with_items("ORD2026080027")
    assert combo.order.order_id == "ORD2026080027"
    assert len(combo.items) == 2


def test_get_order_with_items_raises_not_found(repo):
    with pytest.raises(OrderNotFoundError):
        repo.get_order_with_items("ORD9999-DOES-NOT-EXIST")


def test_get_order_facts_has_no_delay_or_risk_judgment(repo):
    facts = repo.get_order_facts("ORD2026080027")
    assert facts.item_count == 2
    assert facts.total_quantity == 2  # one unit each of two products
    assert not hasattr(facts, "is_delayed")
    assert not hasattr(facts, "risk_level")
    assert not hasattr(facts, "refund_eligible")


def test_get_order_facts_raises_not_found(repo):
    with pytest.raises(OrderNotFoundError):
        repo.get_order_facts("ORD9999-DOES-NOT-EXIST")


def test_get_order_repository_defaults_to_fixture(monkeypatch):
    monkeypatch.delenv("APP_DATA_BACKEND", raising=False)
    assert isinstance(get_order_repository(), FixtureOrderRepository)


def test_get_order_repository_db_backend_without_database_url_raises(monkeypatch):
    monkeypatch.setenv("APP_DATA_BACKEND", "postgres")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError):
        get_order_repository()
