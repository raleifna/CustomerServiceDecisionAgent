from datetime import date

import pytest

from src.backend.repositories.shipment_repository import (
    FixtureShipmentRepository,
    NoShipmentForOrderError,
    ShipmentEvent,
    get_shipment_repository,
)


@pytest.fixture
def repo():
    return FixtureShipmentRepository()


def test_early_delivery_is_not_overdue_and_has_no_delay(repo):
    # ORD2026080001: shipped 08-02, delivered 08-04, estimated 08-05
    facts = repo.get_shipment_facts("ORD2026080001", as_of_date=date(2026, 8, 10))
    assert facts.is_shipped is True
    assert facts.is_delivered is True
    assert facts.is_overdue is False
    assert facts.delay_days is None


def test_late_delivery_reports_delay_days_and_a_logistics_event(repo):
    # ORD2026060004: delivered 2026-07-05, estimated 2026-06-24 -> 11 days late
    facts = repo.get_shipment_facts("ORD2026060004", as_of_date=date(2026, 8, 10))
    assert facts.is_delivered is True
    assert facts.delay_days == 11
    assert facts.unresolved_exception_count == 0  # the one logistics event on this order is resolved
    assert facts.latest_event is not None
    assert isinstance(facts.latest_event, ShipmentEvent)


def test_shipped_undelivered_and_past_estimate_is_overdue(repo):
    # ORD2026080002: shipped, never delivered, estimated 2026-08-08
    facts = repo.get_shipment_facts("ORD2026080002", as_of_date=date(2026, 8, 20))
    assert facts.is_shipped is True
    assert facts.is_delivered is False
    assert facts.is_overdue is True
    assert facts.delay_days == 12  # 2026-08-20 - 2026-08-08
    assert facts.unresolved_exception_count == 1


def test_shipped_but_not_yet_past_estimate_is_not_overdue(repo):
    # ORD2026080023: shipped 08-13, undelivered, estimated 2026-08-18
    facts = repo.get_shipment_facts("ORD2026080023", as_of_date=date(2026, 8, 15))
    assert facts.is_shipped is True
    assert facts.is_delivered is False
    assert facts.is_overdue is False
    assert facts.delay_days is None


def test_never_shipped_and_not_yet_due_is_not_overdue(repo):
    # ORD2026080021: never shipped, estimated 2026-08-22
    facts = repo.get_shipment_facts("ORD2026080021", as_of_date=date(2026, 8, 15))
    assert facts.is_shipped is False
    assert facts.is_delivered is False
    assert facts.is_overdue is False


def test_as_of_date_exactly_on_estimated_date_is_not_yet_overdue(repo):
    # boundary case: as_of_date == estimated_delivery_date must NOT be overdue yet
    facts = repo.get_shipment_facts("ORD2026080023", as_of_date=date(2026, 8, 18))
    assert facts.is_overdue is False
    facts_next_day = repo.get_shipment_facts("ORD2026080023", as_of_date=date(2026, 8, 19))
    assert facts_next_day.is_overdue is True


def test_get_by_order_id_raises_not_found_for_unknown_order(repo):
    with pytest.raises(NoShipmentForOrderError):
        repo.get_by_order_id("ORD9999-DOES-NOT-EXIST")


def test_get_shipment_facts_raises_not_found_for_unknown_order(repo):
    with pytest.raises(NoShipmentForOrderError):
        repo.get_shipment_facts("ORD9999-DOES-NOT-EXIST", as_of_date=date(2026, 8, 10))


def test_get_events_returns_all_events_sorted(repo):
    events = repo.get_events("ORD2026080002")
    assert len(events) == 1
    assert events[0].event_type == "物流停滞超72小时"
    assert events[0].resolved is False


def test_shipment_record_exists_but_is_shipped_is_false_when_never_shipped(repo):
    # the core semantic the whole repository exists to enforce: presence of
    # a (derived) shipment record must never be read as "shipped"
    shipment = repo.get_by_order_id("ORD2026080024")
    assert shipment is not None  # the record itself resolves fine
    assert shipment.shipped_at is None
    facts = repo.get_shipment_facts("ORD2026080024", as_of_date=date(2026, 8, 10))
    assert facts.is_shipped is False


def test_get_shipment_repository_defaults_to_fixture(monkeypatch):
    monkeypatch.delenv("APP_DATA_BACKEND", raising=False)
    assert isinstance(get_shipment_repository(), FixtureShipmentRepository)


def test_get_shipment_repository_db_backend_without_database_url_raises(monkeypatch):
    monkeypatch.setenv("APP_DATA_BACKEND", "postgres")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError):
        get_shipment_repository()
