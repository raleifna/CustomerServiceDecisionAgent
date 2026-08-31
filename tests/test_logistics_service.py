from datetime import date

import pytest

from src.backend.repositories.shipment_repository import (
    NoShipmentForOrderError,
    Shipment,
    ShipmentEvent,
    ShipmentFacts,
    ShipmentWithEvents,
)
from src.backend.services.logistics_service import (
    AVAILABLE,
    REASON_TRACKING_STALL_NOT_PROVIDED,
    REASON_TRANSIT_DETENTION_NOT_PROVIDED,
    UNAVAILABLE,
    FixtureLogisticsProvider,
    PostgresLogisticsProvider,
    get_logistics_provider,
)
from src.backend.services.tool_errors import SHIPMENT_DATA_UNAVAILABLE, ToolError


class _StubShipmentRepository:
    def __init__(self, events, *, is_overdue=False):
        self._events = events
        self._is_overdue = is_overdue

    def get_shipment_with_events(self, order_id):
        shipment = Shipment(
            shipment_id=order_id, order_id=order_id, carrier="X", shipped_at=date(2026, 1, 1),
            delivered_at=None, estimated_delivery_date=date(2026, 1, 5), shipment_status="InTransit",
        )
        return ShipmentWithEvents(shipment=shipment, events=self._events)

    def get_shipment_facts(self, order_id, as_of_date):
        return ShipmentFacts(
            order_id=order_id, shipment_status="InTransit", shipped_at=date(2026, 1, 1),
            delivered_at=None, estimated_delivery_at=date(2026, 1, 5),
            is_shipped=True, is_delivered=False, is_overdue=self._is_overdue,
            delay_days=3 if self._is_overdue else None, latest_event=None,
            unresolved_exception_count=sum(1 for e in self._events if not e.resolved),
            as_of_date=as_of_date,
        )


class _RaisingShipmentRepository:
    def get_shipment_with_events(self, order_id):
        raise NoShipmentForOrderError(order_id)

    def get_shipment_facts(self, order_id, as_of_date):
        raise NoShipmentForOrderError(order_id)


def _event(event_type, resolved, event_time=date(2026, 1, 2)):
    return ShipmentEvent(
        event_id="EVT-1", shipment_id="ORD1", event_type=event_type,
        event_time=event_time, resolved=resolved, source_type="REAL", derivation=None,
    )


# --- FixtureLogisticsProvider: exception-type presence/absence is a real fact ---

def test_fixture_tracking_stalled_signal_is_available_and_true_when_present():
    repo = _StubShipmentRepository([_event("物流停滞超72小时", resolved=False)])
    facts = FixtureLogisticsProvider(repo).get_logistics_facts("ORD1", as_of_date=date(2026, 1, 10))
    assert facts.tracking_stalled_over_72h.availability == AVAILABLE
    assert facts.tracking_stalled_over_72h.value is True
    assert facts.unresolved_tracking_stalled_over_72h.value is True


def test_fixture_tracking_stalled_signal_is_available_and_false_when_absent():
    repo = _StubShipmentRepository([_event("派送延迟", resolved=False)])
    facts = FixtureLogisticsProvider(repo).get_logistics_facts("ORD1", as_of_date=date(2026, 1, 10))
    assert facts.tracking_stalled_over_72h.availability == AVAILABLE
    assert facts.tracking_stalled_over_72h.value is False  # absence IS the fact here


def test_fixture_transit_detained_signal_distinct_from_tracking_stalled():
    repo = _StubShipmentRepository([_event("滞留超72小时", resolved=True)])
    facts = FixtureLogisticsProvider(repo).get_logistics_facts("ORD1", as_of_date=date(2026, 1, 10))
    assert facts.stalled_in_transit_over_72h.value is True
    assert facts.tracking_stalled_over_72h.value is False  # different exception_type, must not conflate
    # resolved=True -- an unresolved-only read must not see it
    assert facts.has_unresolved_exception.value is False


def test_fixture_no_exceptions_at_all_is_available_and_false():
    facts = FixtureLogisticsProvider(_StubShipmentRepository([])).get_logistics_facts(
        "ORD1", as_of_date=date(2026, 1, 10)
    )
    assert facts.has_logistics_exception.availability == AVAILABLE
    assert facts.has_logistics_exception.value is False
    assert facts.tracking_stalled_over_72h.value is False
    assert facts.stalled_in_transit_over_72h.value is False


def test_fixture_delivery_overdue_passthrough_from_shipment_facts():
    facts = FixtureLogisticsProvider(_StubShipmentRepository([], is_overdue=True)).get_logistics_facts(
        "ORD1", as_of_date=date(2026, 1, 10)
    )
    assert facts.delivery_overdue.availability == AVAILABLE
    assert facts.delivery_overdue.value is True
    assert facts.delivery_overdue.source_type == "DERIVED"


# --- PostgresLogisticsProvider: the two exception-type signals are always UNAVAILABLE ---

def test_postgres_tracking_stalled_is_always_unavailable_regardless_of_events():
    repo = _StubShipmentRepository([_event("超过预计送达时间仍未签收", resolved=False)])
    facts = PostgresLogisticsProvider(repo).get_logistics_facts("ORD1", as_of_date=date(2026, 1, 10))
    assert facts.tracking_stalled_over_72h.availability == UNAVAILABLE
    assert facts.tracking_stalled_over_72h.value is None
    assert facts.tracking_stalled_over_72h.reason == REASON_TRACKING_STALL_NOT_PROVIDED


def test_postgres_transit_detained_is_always_unavailable():
    facts = PostgresLogisticsProvider(_StubShipmentRepository([])).get_logistics_facts(
        "ORD1", as_of_date=date(2026, 1, 10)
    )
    assert facts.stalled_in_transit_over_72h.availability == UNAVAILABLE
    assert facts.stalled_in_transit_over_72h.reason == REASON_TRANSIT_DETENTION_NOT_PROVIDED


def test_postgres_delivery_overdue_and_has_exception_stay_available():
    repo = _StubShipmentRepository(
        [_event("超过预计送达时间仍未签收", resolved=False)], is_overdue=True,
    )
    facts = PostgresLogisticsProvider(repo).get_logistics_facts("ORD1", as_of_date=date(2026, 1, 10))
    assert facts.delivery_overdue.availability == AVAILABLE
    assert facts.delivery_overdue.value is True
    assert facts.has_logistics_exception.availability == AVAILABLE
    assert facts.has_logistics_exception.value is True
    assert facts.has_unresolved_exception.value is True


def test_postgres_never_reads_the_fixture_only_exception_strings_as_a_real_false():
    # Even if a Postgres-shaped event list somehow contained the fixture's
    # exact strings (shouldn't happen -- V2 never emits them), the signal
    # must still be UNAVAILABLE, not flip to AVAILABLE/true. Unconditional,
    # not string-matching-dependent -- this is the point of the whole split.
    repo = _StubShipmentRepository([_event("物流停滞超72小时", resolved=False)])
    facts = PostgresLogisticsProvider(repo).get_logistics_facts("ORD1", as_of_date=date(2026, 1, 10))
    assert facts.tracking_stalled_over_72h.availability == UNAVAILABLE


# --- shared: missing shipment record is a real failure, not silent zero ---

def test_missing_shipment_record_raises_tool_error_on_both_providers():
    for provider_cls in (FixtureLogisticsProvider, PostgresLogisticsProvider):
        with pytest.raises(ToolError) as exc_info:
            provider_cls(_RaisingShipmentRepository()).get_logistics_facts(
                "ORD-GONE", as_of_date=date(2026, 1, 10)
            )
        assert exc_info.value.code == SHIPMENT_DATA_UNAVAILABLE


# --- as_dict() serialization shape ---

def test_as_dict_produces_the_documented_signal_shape():
    facts = FixtureLogisticsProvider(_StubShipmentRepository([])).get_logistics_facts(
        "ORD1", as_of_date=date(2026, 1, 10)
    )
    d = facts.as_dict()
    assert d["order_id"] == "ORD1"
    assert d["exceptions"] == []
    assert set(d["delivery_overdue"].keys()) == {"availability", "value", "reason", "source_type"}


# --- factory ---

def test_get_logistics_provider_defaults_to_fixture(monkeypatch):
    monkeypatch.delenv("APP_DATA_BACKEND", raising=False)
    assert isinstance(get_logistics_provider(), FixtureLogisticsProvider)


def test_get_logistics_provider_rejects_unknown_backend(monkeypatch):
    monkeypatch.setenv("APP_DATA_BACKEND", "some-typo")
    with pytest.raises(RuntimeError):
        get_logistics_provider()


# --- against the real fixture data (not stubs) -- the actual orders this
# module's docstring and logistics_agent.py both reference ---

def test_real_fixture_order_with_tracking_stall():
    facts = FixtureLogisticsProvider().get_logistics_facts("ORD2026080002", as_of_date=date(2026, 8, 20))
    assert facts.tracking_stalled_over_72h.value is True
    assert facts.unresolved_tracking_stalled_over_72h.value is True
    assert facts.stalled_in_transit_over_72h.value is False


def test_real_fixture_order_with_resolved_transit_detention():
    facts = FixtureLogisticsProvider().get_logistics_facts("ORD2026060004", as_of_date=date(2026, 8, 20))
    assert facts.stalled_in_transit_over_72h.value is True
    assert facts.has_unresolved_exception.value is False  # resolved=true in the fixture
    assert facts.tracking_stalled_over_72h.value is False
