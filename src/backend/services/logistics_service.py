"""LogisticsProvider: signal-based logistics facts for load_logistics_node
(src/backend/graph/workflow.py) and, downstream, logistics_agent.py /
policy_agent.py's SHIPPING-002/004/REFUND-007/VIP-002/SHIPPING-007 rules.

Every "is X true" fact here is wrapped in a LogisticsSignal instead of a
plain bool, because a plain bool cannot distinguish "no, checked, this
does not apply" from "this data source cannot tell us" -- collapsing the
two produces a silent false negative the instant a caller reads an
UNAVAILABLE fact's absence as a confirmed "no". See query_tool_provider.py's
is_overdue/can_offer None-vs-False precedent for the same principle applied
earlier in this project; the same discipline applies here to booleans, not
only to nullable optional fields.

Two genuinely different capability sets (Fixture / Postgres), not two
implementations of the same capability -- same split rationale as
query_tool_provider.py's FixtureQueryToolProvider/PostgresQueryToolProvider:

  FixtureLogisticsProvider   the 27-order fixture. logistics.json is a
                               complete accounting of every known exception
                               for an order, so the *absence* of a specific
                               exception_type is itself a real fact, not
                               just "we don't know". All signals AVAILABLE.
  PostgresLogisticsProvider   the real Olist/Amazon V2 data. Olist provides
                               neither a continuous carrier-tracking feed
                               nor a "package physically detained somewhere"
                               signal -- shipment_events on this backend
                               NEVER contains "物流停滞超72小时" or "滞留超72
                               小时" event types, whether or not a real stall
                               happened, so their absence there proves
                               nothing. tracking_stalled_over_72h and
                               stalled_in_transit_over_72h report
                               UNAVAILABLE unconditionally on this backend.
                               delivery_overdue and has_logistics_exception/
                               has_unresolved_exception ARE real, computable
                               facts here (real shipped/estimated/delivered
                               dates, real DERIVED shipment_events), so
                               those stay AVAILABLE.

Do NOT conflate "超过预计送达时间仍未签收" (delivery_overdue -- a real,
computable milestone comparison, DERIVED source_type) with "物流停滞超72
小时" (tracking_stalled_over_72h -- a continuous-tracking-feed signal Olist
cannot provide at all). Renaming one into the other would fabricate a fact
the data source does not actually give.
"""

from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

from src.backend.repositories.shipment_repository import (
    NoShipmentForOrderError,
    ShipmentRepository,
    get_shipment_repository,
)
from src.backend.services.tool_errors import SHIPMENT_DATA_UNAVAILABLE, ToolError

AVAILABLE = "AVAILABLE"
UNAVAILABLE = "UNAVAILABLE"

REASON_TRACKING_STALL_NOT_PROVIDED = "CONTINUOUS_TRACKING_NOT_PROVIDED"
REASON_TRANSIT_DETENTION_NOT_PROVIDED = "TRANSIT_DETENTION_NOT_PROVIDED"

# The two exception_type strings this module treats specially -- see the
# module docstring for why their *absence* means different things on the
# two backends. Never rename/repurpose these; they are the exact strings
# the 27-order fixture's data/mock_db/logistics.json uses.
_TRACKING_STALLED_TYPE = "物流停滞超72小时"
_TRANSIT_DETAINED_TYPE = "滞留超72小时"


@dataclass(frozen=True)
class LogisticsSignal:
    availability: str          # AVAILABLE | UNAVAILABLE
    value: bool | None         # None iff availability == UNAVAILABLE
    reason: str | None         # None iff availability == AVAILABLE
    source_type: str | None    # REAL | DERIVED -- None iff availability == UNAVAILABLE

    def as_dict(self) -> dict[str, Any]:
        return {
            "availability": self.availability, "value": self.value,
            "reason": self.reason, "source_type": self.source_type,
        }


def _available(value: bool, source_type: str) -> LogisticsSignal:
    return LogisticsSignal(availability=AVAILABLE, value=value, reason=None, source_type=source_type)


def _unavailable(reason: str) -> LogisticsSignal:
    return LogisticsSignal(availability=UNAVAILABLE, value=None, reason=reason, source_type=None)


@dataclass(frozen=True)
class LogisticsExceptionRecord:
    carrier: str | None
    exception_type: str
    exception_recorded_date: date
    resolved: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "carrier": self.carrier, "exception_type": self.exception_type,
            "exception_recorded_date": self.exception_recorded_date.isoformat(),
            "resolved": self.resolved,
        }


@dataclass(frozen=True)
class LogisticsFacts:
    order_id: str
    exceptions: list[LogisticsExceptionRecord]
    has_logistics_exception: LogisticsSignal
    has_unresolved_exception: LogisticsSignal
    delivery_overdue: LogisticsSignal
    tracking_stalled_over_72h: LogisticsSignal
    unresolved_tracking_stalled_over_72h: LogisticsSignal
    stalled_in_transit_over_72h: LogisticsSignal

    def as_dict(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "exceptions": [e.as_dict() for e in self.exceptions],
            "has_logistics_exception": self.has_logistics_exception.as_dict(),
            "has_unresolved_exception": self.has_unresolved_exception.as_dict(),
            "delivery_overdue": self.delivery_overdue.as_dict(),
            "tracking_stalled_over_72h": self.tracking_stalled_over_72h.as_dict(),
            "unresolved_tracking_stalled_over_72h": self.unresolved_tracking_stalled_over_72h.as_dict(),
            "stalled_in_transit_over_72h": self.stalled_in_transit_over_72h.as_dict(),
        }


class LogisticsProvider(Protocol):
    def get_logistics_facts(self, order_id: str, *, as_of_date: date) -> LogisticsFacts: ...


def _fetch(shipment_repository: ShipmentRepository, order_id: str, as_of_date: date):
    """Shared by both providers -- only the *signal wrapping* differs
    between them, not how shipment/event data is fetched. A missing
    shipment record is treated as a real failure (ToolError), not silently
    downgraded to "zero exceptions" -- every order is expected to have
    exactly one shipment row on both backends (see ShipmentRepository's own
    module docstring) by the time load_logistics_node runs (after
    load_order_node has already confirmed the order itself exists)."""
    try:
        combo = shipment_repository.get_shipment_with_events(order_id)
        facts = shipment_repository.get_shipment_facts(order_id, as_of_date)
    except NoShipmentForOrderError as exc:
        raise ToolError(
            SHIPMENT_DATA_UNAVAILABLE, "No shipment record was found for this order.",
        ) from exc
    return combo, facts


def _exception_records(events) -> list[LogisticsExceptionRecord]:
    return [
        LogisticsExceptionRecord(
            carrier=None, exception_type=e.event_type,
            exception_recorded_date=e.event_time, resolved=e.resolved,
        )
        for e in events
    ]


class FixtureLogisticsProvider:
    def __init__(self, shipment_repository: ShipmentRepository | None = None):
        self._shipment_repository = shipment_repository or get_shipment_repository()

    def get_logistics_facts(self, order_id: str, *, as_of_date: date) -> LogisticsFacts:
        combo, facts = _fetch(self._shipment_repository, order_id, as_of_date)
        events = combo.events
        exception_types = {e.event_type for e in events}
        unresolved_types = {e.event_type for e in events if not e.resolved}

        return LogisticsFacts(
            order_id=order_id,
            exceptions=_exception_records(events),
            has_logistics_exception=_available(len(events) > 0, "REAL"),
            has_unresolved_exception=_available(any(not e.resolved for e in events), "REAL"),
            delivery_overdue=_available(facts.is_overdue, "DERIVED"),
            tracking_stalled_over_72h=_available(_TRACKING_STALLED_TYPE in exception_types, "REAL"),
            unresolved_tracking_stalled_over_72h=_available(
                _TRACKING_STALLED_TYPE in unresolved_types, "REAL"
            ),
            stalled_in_transit_over_72h=_available(_TRANSIT_DETAINED_TYPE in exception_types, "REAL"),
        )


class PostgresLogisticsProvider:
    def __init__(self, shipment_repository: ShipmentRepository | None = None):
        self._shipment_repository = shipment_repository or get_shipment_repository()

    def get_logistics_facts(self, order_id: str, *, as_of_date: date) -> LogisticsFacts:
        combo, facts = _fetch(self._shipment_repository, order_id, as_of_date)
        events = combo.events

        return LogisticsFacts(
            order_id=order_id,
            exceptions=_exception_records(events),
            has_logistics_exception=_available(len(events) > 0, "DERIVED"),
            has_unresolved_exception=_available(any(not e.resolved for e in events), "DERIVED"),
            delivery_overdue=_available(facts.is_overdue, "DERIVED"),
            tracking_stalled_over_72h=_unavailable(REASON_TRACKING_STALL_NOT_PROVIDED),
            unresolved_tracking_stalled_over_72h=_unavailable(REASON_TRACKING_STALL_NOT_PROVIDED),
            stalled_in_transit_over_72h=_unavailable(REASON_TRANSIT_DETENTION_NOT_PROVIDED),
        )


def get_logistics_provider() -> LogisticsProvider:
    import os
    backend = os.getenv("APP_DATA_BACKEND", "fixture").strip().lower()
    if backend == "postgres":
        return PostgresLogisticsProvider()
    if backend == "fixture":
        return FixtureLogisticsProvider()
    raise RuntimeError(f"unknown APP_DATA_BACKEND={backend!r}, expected 'postgres' or 'fixture'")
