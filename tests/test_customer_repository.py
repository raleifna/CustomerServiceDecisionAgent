import os
from datetime import date
from decimal import Decimal

import pytest

from src.backend.repositories.customer_repository import (
    Customer,
    CustomerNotFoundError,
    FixtureCustomerRepository,
    get_customer_repository,
)


@pytest.fixture
def repo():
    return FixtureCustomerRepository()


def test_get_by_id_returns_known_fixture_customer(repo):
    customer = repo.get_by_id("CUS0021")
    assert isinstance(customer, Customer)
    assert customer.customer_id == "CUS0021"
    assert customer.source_unique_id is None  # fixture customers were never externally sourced


def test_get_by_id_raises_not_found_for_unknown_customer(repo):
    with pytest.raises(CustomerNotFoundError):
        repo.get_by_id("CUS9999-DOES-NOT-EXIST")


def test_get_by_source_unique_id_always_not_found_on_fixture(repo):
    # Fixture customers were hand-authored, never sourced from an external
    # dataset -- there is no source_unique_id to match, by design.
    with pytest.raises(CustomerNotFoundError):
        repo.get_by_source_unique_id("any-value-at-all")


def test_list_orders_summary_sorted_newest_first(repo):
    orders = repo.list_orders_summary("CUS0021")
    order_ids = [o.order_id for o in orders]
    assert len(order_ids) == 4
    dates = [o.order_date for o in orders]
    assert dates == sorted(dates, reverse=True)
    assert all(isinstance(o.amount, Decimal) for o in orders)
    # legacy fixture orders.json has no order_status field at all
    assert all(o.order_status is None for o in orders)


def test_list_orders_summary_empty_for_customer_with_no_orders(repo):
    assert repo.list_orders_summary("CUS9999-DOES-NOT-EXIST") == []


def test_get_customer_risk_facts_aggregates_correctly(repo):
    facts = repo.get_customer_risk_facts("CUS0021")
    assert facts.customer_id == "CUS0021"
    assert facts.total_order_count == 4
    assert facts.completed_order_count == 1  # only one of the 4 has a delivered_date
    assert facts.total_spend == Decimal("2955.0")
    assert facts.first_order_date == date(2026, 7, 15)
    assert facts.last_order_date == date(2026, 8, 20)


def test_get_customer_risk_facts_raises_not_found(repo):
    with pytest.raises(CustomerNotFoundError):
        repo.get_customer_risk_facts("CUS9999-DOES-NOT-EXIST")


def test_get_customer_repository_defaults_to_fixture(monkeypatch):
    monkeypatch.delenv("APP_DATA_BACKEND", raising=False)
    assert isinstance(get_customer_repository(), FixtureCustomerRepository)


def test_get_customer_repository_db_backend_without_database_url_raises(monkeypatch):
    monkeypatch.setenv("APP_DATA_BACKEND", "postgres")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError):
        get_customer_repository()


def test_get_customer_repository_rejects_unknown_backend(monkeypatch):
    monkeypatch.setenv("APP_DATA_BACKEND", "some-typo")
    with pytest.raises(RuntimeError):
        get_customer_repository()
