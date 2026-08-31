from datetime import date

import pytest

from src.backend.services.query_tool_provider import (
    AVAILABLE,
    UNAVAILABLE,
    FixtureQueryToolProvider,
    get_query_tool_provider,
)


@pytest.fixture
def provider():
    return FixtureQueryToolProvider()


def test_get_order_status_with_promised_ship_date_marks_expedite_available(provider):
    # ORD2026080021: never shipped, promised_ship_date=2026-08-18
    result = provider.get_order_status("ORD2026080021", as_of_date=date(2026, 8, 20))
    assert result["expedite"]["availability"] == AVAILABLE
    assert result["expedite"]["can_offer"] is True  # not shipped + has a promised date
    assert result["expedite"]["is_overdue"] is True  # promised date already passed as_of_date


def test_get_order_status_unknown_order_returns_none(provider):
    assert provider.get_order_status("ORD9999-DOES-NOT-EXIST", as_of_date=date(2026, 8, 20)) is None


def test_get_logistics_status_with_real_tracking_marks_available(provider):
    result = provider.get_logistics_status("ORD2026080002", as_of_date=date(2026, 8, 20))
    assert result["tracking"]["availability"] == AVAILABLE
    assert result["tracking"]["current_location"] is not None
    assert result["is_overdue"] is True


def test_can_offer_is_none_not_false_when_unavailable():
    # a hand-built order dict with no promised_ship_date at all -- exercised
    # via a minimal stand-in rather than hunting for a fixture order lacking
    # the field, since every current fixture order happens to be shipped or
    # to carry one.
    from src.backend.services import query_tool_provider as mod

    class _StubOrderNoPromise:
        pass

    original_get_order = mod.mock_db_repository.get_order
    mod.mock_db_repository.get_order = lambda order_id: {
        "order_id": order_id, "payment_status": "Paid",
        "shipped_date": None, "delivered_date": None,
        "estimated_delivery_date": None, "promised_ship_date": None,
    }
    try:
        provider = FixtureQueryToolProvider()
        result = provider.get_order_status("ORD-STUB", as_of_date=date(2026, 8, 20))
    finally:
        mod.mock_db_repository.get_order = original_get_order

    assert result["expedite"]["availability"] == UNAVAILABLE
    assert result["expedite"]["can_offer"] is None  # not False -- "we don't know", not "no"
    assert result["expedite"]["is_overdue"] is None  # not False -- can't judge without a promised date


def test_get_query_tool_provider_defaults_to_fixture(monkeypatch):
    monkeypatch.delenv("APP_DATA_BACKEND", raising=False)
    assert isinstance(get_query_tool_provider(), FixtureQueryToolProvider)


def test_get_query_tool_provider_rejects_unknown_backend(monkeypatch):
    monkeypatch.setenv("APP_DATA_BACKEND", "some-typo")
    with pytest.raises(RuntimeError):
        get_query_tool_provider()
