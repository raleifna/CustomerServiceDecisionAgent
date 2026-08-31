"""Order read repository: same Protocol + two-implementation +
explicit-APP_DATA_BACKEND pattern as customer_repository.py /
product_repository.py.

Scope boundary this module exists to enforce -- do not blur it when wiring
callers:
  - This repository owns `orders` + `order_items` ONLY. It never queries
    `shipments`/`shipment_events` (that's ShipmentRepository) or `payments`
    (that's PaymentRepository) -- those are separate modules on purpose, so
    they stay meaningful on their own instead of folding into an
    "OrderRepository that knows everything".
  - It never interprets "shipment exists" as "shipped" -- shipping status
    lives on ShipmentRepository's shipment_status/shipped_at, this module
    only exposes orders.shipped_date/delivered_date as raw facts.
  - It never judges delay, refund eligibility, or risk level. order_status
    is passed through verbatim (canceled/processing/invoiced/delivered/...);
    no status is coerced or reinterpreted.
  - Product details (title, catalog price) are NOT joined in here -- a
    caller building an order detail view calls ProductRepository.
    list_by_ids() with the item's product_ids, batched, not this module.
  - Combining Order + Product + Shipment + Payment into one view is
    OrderContextService's job (a later, separate module), which composes
    OrderRepository + ProductRepository + ShipmentRepository +
    PaymentRepository. Agent Tools call OrderContextService, never chain all
    four repositories themselves.

`filename` note: `order_item_repository.py` already exists in this
directory but is a narrow, Postgres-only helper for looking up a single
order_item by id (used by the after-sales write path in
service_request_repository.py) -- a different, older, narrower thing from
this module's Order+OrderItem read Protocol. Both stay; this one is not a
replacement for it.
"""

import os
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol

from src.backend.repositories import mock_db_repository
from src.backend.repositories.postgres_repository import get_database_url


class OrderNotFoundError(LookupError):
    """No order matches the given id in the selected backend."""


@dataclass(frozen=True)
class Order:
    order_id: str
    customer_id: str
    order_status: str | None          # None on the fixture backend (no such column there)
    payment_status: str
    refund_status: str | None         # None on the fixture backend; synthetic where present (see products/customers modules)
    order_date: date
    shipped_date: date | None
    delivered_date: date | None
    estimated_delivery_date: date | None
    delivery_recipient_type: str | None
    amount: Decimal                   # CNY, already converted -- see rounding_adjustment
    rounding_adjustment: Decimal      # amount - sum(order_items.paid_amount); 0 on the fixture backend


@dataclass(frozen=True)
class OrderItem:
    order_item_id: str
    order_id: str
    product_id: str
    sku_id: str
    sku_name: str
    unit_price: Decimal
    quantity: int
    discount_amount: Decimal
    paid_amount: Decimal              # the real transaction amount for this line -- see product_repository.py's price boundary note
    shipped_product_id: str | None
    shipped_quantity: int | None


@dataclass(frozen=True)
class OrderWithItems:
    order: Order
    items: list[OrderItem]


@dataclass(frozen=True)
class OrderFacts:
    """Facts only -- no delay/refund-eligibility/risk judgment. That's
    Evidence Gate / Policy Agent's job, working from these facts plus
    ShipmentRepository/PaymentRepository/CustomerRepository facts."""
    order_id: str
    order_status: str | None
    item_count: int
    total_quantity: int
    amount: Decimal
    rounding_adjustment: Decimal
    order_date: date
    delivered_date: date | None


class OrderRepository(Protocol):
    def get_by_id(self, order_id: str) -> Order: ...
    def list_by_customer_id(self, customer_id: str) -> list[Order]: ...
    def get_order_items(self, order_id: str) -> list[OrderItem]: ...
    def get_order_with_items(self, order_id: str) -> OrderWithItems: ...
    def get_order_facts(self, order_id: str) -> OrderFacts: ...


class PostgresOrderRepository:
    """Reads the real `orders` + `order_items` tables. Raises
    OrderNotFoundError for a missing id; lets any psycopg connection/query
    error propagate unmodified -- a database outage must be visible, not
    masked by a fallback to fixture data."""

    def __init__(self, database_url: str):
        self._database_url = database_url

    def _connect(self):
        import psycopg
        from psycopg.rows import dict_row
        return psycopg.connect(self._database_url, row_factory=dict_row)

    def get_by_id(self, order_id: str) -> Order:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT order_id, customer_id, order_status, payment_status, refund_status, "
                "order_date, shipped_date, delivered_date, estimated_delivery_date, "
                "delivery_recipient_type, amount, rounding_adjustment "
                "FROM orders WHERE order_id = %s",
                (order_id,),
            )
            row = cur.fetchone()
        if row is None:
            raise OrderNotFoundError(order_id)
        return _order_from_row(row)

    def list_by_customer_id(self, customer_id: str) -> list[Order]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT order_id, customer_id, order_status, payment_status, refund_status, "
                "order_date, shipped_date, delivered_date, estimated_delivery_date, "
                "delivery_recipient_type, amount, rounding_adjustment "
                "FROM orders WHERE customer_id = %s ORDER BY order_date DESC",
                (customer_id,),
            )
            rows = cur.fetchall()
        return [_order_from_row(r) for r in rows]

    def get_order_items(self, order_id: str) -> list[OrderItem]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT order_item_id, order_id, product_id, sku_id, sku_name, unit_price, "
                "quantity, discount_amount, paid_amount, shipped_product_id, shipped_quantity "
                "FROM order_items WHERE order_id = %s ORDER BY order_item_id",
                (order_id,),
            )
            rows = cur.fetchall()
        return [_order_item_from_row(r) for r in rows]

    def get_order_with_items(self, order_id: str) -> OrderWithItems:
        # Two queries (order, then its items) -- not N+1, since this is one
        # order's items, not a loop over many orders. A caller that needs
        # this for many orders should call get_order_items per order_id it
        # already has, or (future work) a batched variant; it must not loop
        # get_by_id per order either way.
        order = self.get_by_id(order_id)
        items = self.get_order_items(order_id)
        return OrderWithItems(order=order, items=items)

    def get_order_facts(self, order_id: str) -> OrderFacts:
        order = self.get_by_id(order_id)
        items = self.get_order_items(order_id)
        return OrderFacts(
            order_id=order.order_id, order_status=order.order_status,
            item_count=len(items), total_quantity=sum(i.quantity for i in items),
            amount=order.amount, rounding_adjustment=order.rounding_adjustment,
            order_date=order.order_date, delivered_date=order.delivered_date,
        )


def _order_from_row(row: dict) -> Order:
    return Order(
        order_id=row["order_id"], customer_id=row["customer_id"],
        order_status=row["order_status"], payment_status=row["payment_status"],
        refund_status=row["refund_status"], order_date=row["order_date"],
        shipped_date=row["shipped_date"], delivered_date=row["delivered_date"],
        estimated_delivery_date=row["estimated_delivery_date"],
        delivery_recipient_type=row["delivery_recipient_type"],
        amount=Decimal(row["amount"]), rounding_adjustment=Decimal(row["rounding_adjustment"]),
    )


def _order_item_from_row(row: dict) -> OrderItem:
    return OrderItem(
        order_item_id=row["order_item_id"], order_id=row["order_id"],
        product_id=row["product_id"], sku_id=row["sku_id"], sku_name=row["sku_name"],
        unit_price=Decimal(row["unit_price"]), quantity=row["quantity"],
        discount_amount=Decimal(row["discount_amount"]), paid_amount=Decimal(row["paid_amount"]),
        shipped_product_id=row["shipped_product_id"], shipped_quantity=row["shipped_quantity"],
    )


class FixtureOrderRepository:
    """Reads the 27-order hand-built JSON fixture (data/mock_db/orders.json
    + order_items.json). Used by the deterministic eval harness and as the
    default when DATABASE_URL isn't configured.

    Known, deliberate gaps versus Postgres: the legacy fixture schema has no
    order_status or refund_status columns at all, and rounding_adjustment
    doesn't exist there either (the fixture's amounts were never run through
    a currency conversion) -- both are always None/0 on this backend, not a
    dropped real value.
    """

    def get_by_id(self, order_id: str) -> Order:
        row = mock_db_repository.get_order(order_id)
        if row is None:
            raise OrderNotFoundError(order_id)
        return _order_from_fixture_row(row)

    def list_by_customer_id(self, customer_id: str) -> list[Order]:
        rows = mock_db_repository.get_orders_by_customer_id(customer_id)
        orders = [_order_from_fixture_row(r) for r in rows]
        return sorted(orders, key=lambda o: o.order_date, reverse=True)

    def get_order_items(self, order_id: str) -> list[OrderItem]:
        rows = mock_db_repository.get_order_items(order_id)
        return [_order_item_from_fixture_row(r) for r in rows]

    def get_order_with_items(self, order_id: str) -> OrderWithItems:
        order = self.get_by_id(order_id)
        items = self.get_order_items(order_id)
        return OrderWithItems(order=order, items=items)

    def get_order_facts(self, order_id: str) -> OrderFacts:
        order = self.get_by_id(order_id)
        items = self.get_order_items(order_id)
        return OrderFacts(
            order_id=order.order_id, order_status=order.order_status,
            item_count=len(items), total_quantity=sum(i.quantity for i in items),
            amount=order.amount, rounding_adjustment=order.rounding_adjustment,
            order_date=order.order_date, delivered_date=order.delivered_date,
        )


def _order_from_fixture_row(row: dict) -> Order:
    def parse_date(value):
        return date.fromisoformat(value) if value else None

    return Order(
        order_id=row["order_id"], customer_id=row["customer_id"],
        order_status=None, payment_status=row["payment_status"], refund_status=None,
        order_date=date.fromisoformat(row["order_date"]),
        shipped_date=parse_date(row.get("shipped_date")),
        delivered_date=parse_date(row.get("delivered_date")),
        estimated_delivery_date=parse_date(row.get("estimated_delivery_date")),
        delivery_recipient_type=row.get("delivery_recipient_type"),
        amount=Decimal(str(row["amount"])), rounding_adjustment=Decimal("0"),
    )


def _order_item_from_fixture_row(row: dict) -> OrderItem:
    return OrderItem(
        order_item_id=row["order_item_id"], order_id=row["order_id"],
        product_id=row["product_id"], sku_id=row["sku_id"], sku_name=row["sku_name"],
        unit_price=Decimal(str(row["unit_price"])), quantity=row["quantity"],
        discount_amount=Decimal(str(row.get("discount_amount", 0))),
        paid_amount=Decimal(str(row["paid_amount"])),
        shipped_product_id=row.get("shipped_product_id"), shipped_quantity=row.get("shipped_quantity"),
    )


def get_order_repository() -> OrderRepository:
    """Same explicit-selection contract as get_customer_repository() /
    get_product_repository(): no implicit fallback between backends."""
    backend = os.getenv("APP_DATA_BACKEND", "fixture").strip().lower()
    if backend == "postgres":
        database_url = get_database_url()
        if not database_url:
            raise RuntimeError("APP_DATA_BACKEND=postgres requires DATABASE_URL to be set")
        return PostgresOrderRepository(database_url)
    if backend == "fixture":
        return FixtureOrderRepository()
    raise RuntimeError(f"unknown APP_DATA_BACKEND={backend!r}, expected 'postgres' or 'fixture'")
