"""Shipment read repository: same Protocol + two-implementation +
explicit-APP_DATA_BACKEND pattern as customer_repository.py /
product_repository.py / order_repository.py.

Scope boundary this module exists to enforce -- do not blur it when wiring
callers:
  - This repository owns `shipments` + `shipment_events` ONLY. On the
    Postgres backend it never queries `orders` -- see the two distinct
    exception types below for why that's still safe to do without losing
    "order not found" vs "no shipment" as separate failure modes.
  - Semantics settled in review, non-negotiable: `shipments` is a
    FULFILLMENT RECORD, not a physical-package record. Every order gets
    exactly one row on the Postgres backend, including canceled/never-
    shipped orders (shipment_status='Canceled', shipped_at/delivered_at
    both NULL). "a shipment row exists" must NEVER be read as "this order
    shipped" -- always check shipment_status / shipped_at instead.
  - No `date.today()` / wall-clock read anywhere in this module.
    get_shipment_facts() takes an explicit `as_of_date` parameter for every
    time-relative computation (is_overdue, delay_days) -- this is the
    BusinessClock role described in earlier review rounds, satisfied here
    by explicit injection rather than a separate service class.
  - Only deterministic, fact-level derivations happen here (is_shipped,
    is_delivered, is_overdue, delay_days, unresolved_exception_count).
    Whether an overdue/undelivered shipment makes a request eligible for
    anything is Evidence Gate / Policy Agent's call, not this module's.
"""

import os
from dataclasses import dataclass
from datetime import date
from typing import Protocol

from src.backend.repositories import mock_db_repository
from src.backend.repositories.postgres_repository import get_database_url

SHIPPED_STATUSES = {"InTransit", "Delivered", "Overdue"}  # all have shipped_at set


class ShipmentNotFoundError(LookupError):
    """The given shipment_id doesn't exist. Distinct from
    NoShipmentForOrderError: this means the id itself is wrong, not that an
    order legitimately has no shipment on record."""


class NoShipmentForOrderError(LookupError):
    """The given order_id has no shipment row (or, on the fixture backend,
    no shipment concept at all -- see FixtureShipmentRepository). Distinct
    from ShipmentNotFoundError: the order_id itself may be perfectly valid,
    it simply isn't tracked here."""


@dataclass(frozen=True)
class ShipmentEvent:
    event_id: str
    shipment_id: str
    event_type: str
    event_time: date
    resolved: bool
    source_type: str            # 'REAL' | 'DERIVED' | 'SYNTHETIC_EVENT'
    derivation: str | None      # human-readable provenance note, carried through verbatim


@dataclass(frozen=True)
class Shipment:
    shipment_id: str
    order_id: str
    carrier: str | None
    shipped_at: date | None
    delivered_at: date | None
    estimated_delivery_date: date | None
    shipment_status: str        # 'Pending' | 'InTransit' | 'Delivered' | 'Overdue' | 'Canceled'


@dataclass(frozen=True)
class ShipmentWithEvents:
    shipment: Shipment
    events: list[ShipmentEvent]   # sorted by (event_time, event_id) -- stable for same-day events


@dataclass(frozen=True)
class ShipmentFacts:
    """Deterministic facts and derived values only -- no refund/risk
    judgment. `as_of_date` is echoed back so a caller/log can see exactly
    what reference date the is_overdue/delay_days computation used."""
    order_id: str
    shipment_status: str
    shipped_at: date | None
    delivered_at: date | None
    estimated_delivery_at: date | None
    is_shipped: bool
    is_delivered: bool
    is_overdue: bool
    delay_days: int | None
    latest_event: ShipmentEvent | None
    unresolved_exception_count: int
    as_of_date: date


class ShipmentRepository(Protocol):
    def get_by_id(self, shipment_id: str) -> Shipment: ...
    def get_by_order_id(self, order_id: str) -> Shipment: ...
    def get_events(self, shipment_id: str) -> list[ShipmentEvent]: ...
    def get_shipment_with_events(self, order_id: str) -> ShipmentWithEvents: ...
    def get_shipment_facts(self, order_id: str, as_of_date: date) -> ShipmentFacts: ...


def _compute_facts(shipment: Shipment, events: list[ShipmentEvent], as_of_date: date) -> ShipmentFacts:
    is_shipped = shipment.shipped_at is not None
    is_delivered = shipment.delivered_at is not None

    # Strict "> " not ">=": the estimated-delivery day itself is not yet
    # overdue (boundary case called out explicitly in review).
    is_overdue = (
        not is_delivered
        and shipment.shipment_status != "Canceled"
        and shipment.estimated_delivery_date is not None
        and as_of_date > shipment.estimated_delivery_date
    )

    delay_days: int | None = None
    if is_delivered and shipment.estimated_delivery_date and shipment.delivered_at > shipment.estimated_delivery_date:
        delay_days = (shipment.delivered_at - shipment.estimated_delivery_date).days
    elif is_overdue:
        delay_days = (as_of_date - shipment.estimated_delivery_date).days

    ordered_events = sorted(events, key=lambda e: (e.event_time, e.event_id))
    latest_event = ordered_events[-1] if ordered_events else None
    unresolved_exception_count = sum(1 for e in events if not e.resolved)

    # The Postgres backend's stored shipment_status is already "Overdue"
    # when applicable (computed at import time against its own frozen
    # anchor) -- this is a no-op there. The fixture backend has no such
    # precomputed status (it derives only Pending/InTransit/Delivered from
    # raw dates, see FixtureShipmentRepository), so this is where an
    # in-transit fixture shipment that's actually overdue as of the
    # caller's as_of_date gets corrected for the facts view.
    effective_status = shipment.shipment_status
    if is_overdue and effective_status not in ("Overdue", "Canceled", "Delivered"):
        effective_status = "Overdue"

    return ShipmentFacts(
        order_id=shipment.order_id, shipment_status=effective_status,
        shipped_at=shipment.shipped_at, delivered_at=shipment.delivered_at,
        estimated_delivery_at=shipment.estimated_delivery_date,
        is_shipped=is_shipped, is_delivered=is_delivered, is_overdue=is_overdue,
        delay_days=delay_days, latest_event=latest_event,
        unresolved_exception_count=unresolved_exception_count, as_of_date=as_of_date,
    )


class PostgresShipmentRepository:
    """Reads the real `shipments` + `shipment_events` tables only -- never
    `orders`. Lets any psycopg connection/query error propagate unmodified
    -- a database outage must be visible, not masked by a fallback to
    fixture data."""

    def __init__(self, database_url: str):
        self._database_url = database_url

    def _connect(self):
        import psycopg
        from psycopg.rows import dict_row
        return psycopg.connect(self._database_url, row_factory=dict_row)

    def get_by_id(self, shipment_id: str) -> Shipment:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT shipment_id, order_id, carrier, shipped_at, delivered_at, "
                "estimated_delivery_date, shipment_status FROM shipments WHERE shipment_id = %s",
                (shipment_id,),
            )
            row = cur.fetchone()
        if row is None:
            raise ShipmentNotFoundError(shipment_id)
        return _shipment_from_row(row)

    def get_by_order_id(self, order_id: str) -> Shipment:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT shipment_id, order_id, carrier, shipped_at, delivered_at, "
                "estimated_delivery_date, shipment_status FROM shipments WHERE order_id = %s",
                (order_id,),
            )
            row = cur.fetchone()
        if row is None:
            raise NoShipmentForOrderError(order_id)
        return _shipment_from_row(row)

    def get_events(self, shipment_id: str) -> list[ShipmentEvent]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT event_id, shipment_id, event_type, event_time, resolved, "
                "source_type, derivation FROM shipment_events WHERE shipment_id = %s "
                "ORDER BY event_time, event_id",
                (shipment_id,),
            )
            rows = cur.fetchall()
        return [_event_from_row(r) for r in rows]

    def get_shipment_with_events(self, order_id: str) -> ShipmentWithEvents:
        shipment = self.get_by_order_id(order_id)
        events = self.get_events(shipment.shipment_id)
        return ShipmentWithEvents(shipment=shipment, events=events)

    def get_shipment_facts(self, order_id: str, as_of_date: date) -> ShipmentFacts:
        combo = self.get_shipment_with_events(order_id)
        return _compute_facts(combo.shipment, combo.events, as_of_date)


def _shipment_from_row(row: dict) -> Shipment:
    return Shipment(
        shipment_id=row["shipment_id"], order_id=row["order_id"], carrier=row["carrier"],
        shipped_at=row["shipped_at"], delivered_at=row["delivered_at"],
        estimated_delivery_date=row["estimated_delivery_date"], shipment_status=row["shipment_status"],
    )


def _event_from_row(row: dict) -> ShipmentEvent:
    return ShipmentEvent(
        event_id=row["event_id"], shipment_id=row["shipment_id"], event_type=row["event_type"],
        event_time=row["event_time"], resolved=row["resolved"],
        source_type=row["source_type"], derivation=row["derivation"],
    )


class FixtureShipmentRepository:
    """The legacy fixture schema has no `shipments` table at all --
    shipping facts live directly on orders.json's shipped_date/
    delivered_date/estimated_delivery_date, and the closest thing to
    shipment_events is logistics.json's per-order exception rows. This
    repository derives an equivalent Shipment/ShipmentEvent view from those
    on every call rather than reading a separate stored table, because
    there genuinely isn't one to read -- this is not the same thing as
    OrderRepository/PaymentRepository reaching into each other's tables.

    Known, deliberate gap: the fixture has no order_status field at all, so
    there is no way to represent a canceled-and-never-shipped order here --
    every derived shipment_status is one of Pending/InTransit/Delivered/
    Overdue, never Canceled. That scenario is only exercisable against the
    real Olist/Amazon batch (Postgres backend), which does carry a real
    order_status.
    """

    def get_by_id(self, shipment_id: str) -> Shipment:
        # The fixture has no separate shipment_id namespace -- by
        # convention (and to give get_by_id *some* meaning here) it accepts
        # the underlying order_id directly.
        return self.get_by_order_id(shipment_id)

    def get_by_order_id(self, order_id: str) -> Shipment:
        order = mock_db_repository.get_order(order_id)
        if order is None:
            raise NoShipmentForOrderError(order_id)
        return _derive_fixture_shipment(order)

    def get_events(self, shipment_id: str) -> list[ShipmentEvent]:
        rows = mock_db_repository.get_logistics(shipment_id)  # shipment_id == order_id here
        events = [_event_from_fixture_logistics(r, idx) for idx, r in enumerate(rows, start=1)]
        return sorted(events, key=lambda e: (e.event_time, e.event_id))

    def get_shipment_with_events(self, order_id: str) -> ShipmentWithEvents:
        shipment = self.get_by_order_id(order_id)
        events = self.get_events(order_id)
        return ShipmentWithEvents(shipment=shipment, events=events)

    def get_shipment_facts(self, order_id: str, as_of_date: date) -> ShipmentFacts:
        combo = self.get_shipment_with_events(order_id)
        return _compute_facts(combo.shipment, combo.events, as_of_date)


def _derive_fixture_shipment(order: dict) -> Shipment:
    def parse_date(value):
        return date.fromisoformat(value) if value else None

    shipped_at = parse_date(order.get("shipped_date"))
    delivered_at = parse_date(order.get("delivered_date"))
    estimated = parse_date(order.get("estimated_delivery_date"))

    if delivered_at:
        status = "Delivered"
    elif shipped_at:
        status = "InTransit"  # "Overdue" needs an as_of_date the Shipment object itself doesn't carry;
        # get_shipment_facts() re-derives the overdue distinction with the caller's as_of_date instead.
    else:
        status = "Pending"

    return Shipment(
        shipment_id=order["order_id"], order_id=order["order_id"],
        carrier=None, shipped_at=shipped_at, delivered_at=delivered_at,
        estimated_delivery_date=estimated, shipment_status=status,
    )


def _event_from_fixture_logistics(row: dict, index: int) -> ShipmentEvent:
    return ShipmentEvent(
        event_id=f"{row['order_id']}-LOG-{index:03d}", shipment_id=row["order_id"],
        event_type=row["exception_type"],
        event_time=date.fromisoformat(row["exception_recorded_date"]),
        resolved=bool(row["resolved"]), source_type="REAL", derivation=None,
    )


def get_shipment_repository() -> ShipmentRepository:
    """Same explicit-selection contract as the sibling repositories: no
    implicit fallback between backends."""
    backend = os.getenv("APP_DATA_BACKEND", "fixture").strip().lower()
    if backend == "postgres":
        database_url = get_database_url()
        if not database_url:
            raise RuntimeError("APP_DATA_BACKEND=postgres requires DATABASE_URL to be set")
        return PostgresShipmentRepository(database_url)
    if backend == "fixture":
        return FixtureShipmentRepository()
    raise RuntimeError(f"unknown APP_DATA_BACKEND={backend!r}, expected 'postgres' or 'fixture'")
