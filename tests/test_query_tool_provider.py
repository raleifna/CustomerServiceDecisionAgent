from datetime import date

import pytest

from src.backend.services.order_context_service import (
    OrderContextIntegrityError,
    OrderContextNotFoundError,
)
from src.backend.services.query_tool_provider import (
    AVAILABLE,
    UNAVAILABLE,
    FixtureQueryToolProvider,
    PostgresQueryToolProvider,
    get_query_tool_provider,
)
from src.backend.services.tool_errors import DATABASE_UNAVAILABLE, DATA_INTEGRITY_ERROR, ORDER_NOT_FOUND, ToolError


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


class _RaisingContextService:
    """Stub standing in for OrderContextService so these tests exercise
    PostgresQueryToolProvider's error translation without a real database."""

    def __init__(self, to_raise: Exception):
        self._to_raise = to_raise
        self.order_repository = self

    def get_order_context(self, order_id, *, as_of_date):
        raise self._to_raise

    def list_by_customer_id(self, customer_id):
        raise self._to_raise


def test_get_order_status_returns_none_on_not_found():
    provider = PostgresQueryToolProvider(_RaisingContextService(OrderContextNotFoundError("ORD-X")))
    assert provider.get_order_status("ORD-X", as_of_date=date(2026, 8, 20)) is None


def test_get_logistics_status_raises_order_not_found_as_tool_error():
    provider = PostgresQueryToolProvider(_RaisingContextService(OrderContextNotFoundError("ORD-X")))
    with pytest.raises(ToolError) as exc_info:
        provider.get_logistics_status("ORD-X", as_of_date=date(2026, 8, 20))
    assert exc_info.value.code == ORDER_NOT_FOUND
    assert exc_info.value.retryable is False


def test_get_order_status_translates_integrity_error():
    provider = PostgresQueryToolProvider(
        _RaisingContextService(OrderContextIntegrityError("ORD-X", ["PRO-MISSING"]))
    )
    with pytest.raises(ToolError) as exc_info:
        provider.get_order_status("ORD-X", as_of_date=date(2026, 8, 20))
    assert exc_info.value.code == DATA_INTEGRITY_ERROR
    assert exc_info.value.retryable is False


def test_list_orders_for_customer_translates_database_unavailable():
    import psycopg

    provider = PostgresQueryToolProvider(
        _RaisingContextService(psycopg.OperationalError("connection refused"))
    )
    with pytest.raises(ToolError) as exc_info:
        provider.list_orders_for_customer("CUS-X")
    assert exc_info.value.code == DATABASE_UNAVAILABLE
    assert exc_info.value.retryable is True
