"""Customer-facing read repository: two independent implementations behind
one Protocol, chosen explicitly via APP_DATA_BACKEND -- never a runtime
try/except fallback between them. A database outage must surface as a clear
error, not silently degrade to fixture data (see get_customer_repository()).

  PostgresCustomerRepository  -- reads the V2 `customers`/`orders` tables
                                  populated by db/migrations/import_olist_batch.py
  FixtureCustomerRepository   -- reads the 27-order hand-built JSON fixture
                                  (data/mock_db/customers.json + orders.json),
                                  used by the deterministic eval harness and
                                  by default wherever DATABASE_URL isn't set

Risk judgment (high/low risk, etc.) is explicitly OUT of scope here --
get_customer_risk_facts() returns facts only. That judgment belongs to the
Policy/Risk layer (policy_agent.py / the future risk_assessments write path),
which decides what a given canceled_order_count or total_spend *means*.
"""

import os
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol

from src.backend.repositories import mock_db_repository
from src.backend.repositories.postgres_repository import get_database_url


class CustomerNotFoundError(LookupError):
    """No customer matches the given id in the selected backend."""


@dataclass(frozen=True)
class Customer:
    customer_id: str
    vip_level: str
    register_date: date | None
    canceled_order_count: int
    source_unique_id: str | None = None  # None for fixture customers (never externally sourced)


@dataclass(frozen=True)
class OrderSummary:
    order_id: str
    order_status: str | None  # None on the fixture backend, which has no order_status field
    order_date: date
    amount: Decimal


@dataclass(frozen=True)
class CustomerRiskFacts:
    """Pure facts for the Policy/Risk layer to interpret -- no verdict."""
    customer_id: str
    vip_level: str
    canceled_order_count: int
    total_order_count: int
    completed_order_count: int
    total_spend: Decimal
    first_order_date: date | None
    last_order_date: date | None


class CustomerRepository(Protocol):
    def get_by_id(self, customer_id: str) -> Customer: ...
    def get_by_source_unique_id(self, source_unique_id: str) -> Customer: ...
    def list_orders_summary(self, customer_id: str) -> list[OrderSummary]: ...
    def get_customer_risk_facts(self, customer_id: str) -> CustomerRiskFacts: ...


class PostgresCustomerRepository:
    """Reads the real transaction fact layer. Raises CustomerNotFoundError
    for a missing id and lets any psycopg connection/query error propagate
    unmodified -- a database outage must be visible, not masked."""

    def __init__(self, database_url: str):
        self._database_url = database_url

    def _connect(self):
        import psycopg
        from psycopg.rows import dict_row
        return psycopg.connect(self._database_url, row_factory=dict_row)

    def get_by_id(self, customer_id: str) -> Customer:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT customer_id, vip_level, register_date, canceled_order_count, "
                "source_unique_id FROM customers WHERE customer_id = %s",
                (customer_id,),
            )
            row = cur.fetchone()
        if row is None:
            raise CustomerNotFoundError(customer_id)
        return _customer_from_row(row)

    def get_by_source_unique_id(self, source_unique_id: str) -> Customer:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT customer_id, vip_level, register_date, canceled_order_count, "
                "source_unique_id FROM customers WHERE source_unique_id = %s",
                (source_unique_id,),
            )
            row = cur.fetchone()
        if row is None:
            raise CustomerNotFoundError(source_unique_id)
        return _customer_from_row(row)

    def list_orders_summary(self, customer_id: str) -> list[OrderSummary]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT order_id, order_status, order_date, amount FROM orders "
                "WHERE customer_id = %s ORDER BY order_date DESC",
                (customer_id,),
            )
            rows = cur.fetchall()
        return [
            OrderSummary(order_id=r["order_id"], order_status=r["order_status"],
                         order_date=r["order_date"], amount=Decimal(r["amount"]))
            for r in rows
        ]

    def get_customer_risk_facts(self, customer_id: str) -> CustomerRiskFacts:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT customer_id, vip_level, canceled_order_count FROM customers WHERE customer_id = %s",
                (customer_id,),
            )
            customer_row = cur.fetchone()
            if customer_row is None:
                raise CustomerNotFoundError(customer_id)
            cur.execute(
                """
                SELECT
                    count(*) AS total_order_count,
                    count(*) FILTER (WHERE order_status = 'delivered') AS completed_order_count,
                    COALESCE(SUM(amount) FILTER (WHERE order_status NOT IN ('canceled', 'unavailable')), 0) AS total_spend,
                    MIN(order_date) AS first_order_date,
                    MAX(order_date) AS last_order_date
                FROM orders WHERE customer_id = %s
                """,
                (customer_id,),
            )
            agg = cur.fetchone()
        return CustomerRiskFacts(
            customer_id=customer_row["customer_id"],
            vip_level=customer_row["vip_level"],
            canceled_order_count=customer_row["canceled_order_count"],
            total_order_count=agg["total_order_count"],
            completed_order_count=agg["completed_order_count"],
            total_spend=Decimal(agg["total_spend"]),
            first_order_date=agg["first_order_date"],
            last_order_date=agg["last_order_date"],
        )


def _customer_from_row(row: dict) -> Customer:
    return Customer(
        customer_id=row["customer_id"], vip_level=row["vip_level"],
        register_date=row["register_date"], canceled_order_count=row["canceled_order_count"],
        source_unique_id=row["source_unique_id"],
    )


class FixtureCustomerRepository:
    """Reads the 27-order hand-built JSON fixture. Used by the deterministic
    eval harness and as the default when DATABASE_URL isn't configured.

    Two known, deliberate gaps versus the Postgres implementation:
      - get_by_source_unique_id always raises CustomerNotFoundError: fixture
        customers were hand-authored, never sourced from an external
        dataset, so there is no source_unique_id to match against.
      - OrderSummary.order_status is always None and CustomerRiskFacts.
        completed_order_count is derived from delivered_date IS NOT NULL
        instead of an order_status column: the legacy fixture orders.json
        schema has no order_status field at all (see data/mock_db/orders.json).
    """

    def get_by_id(self, customer_id: str) -> Customer:
        row = mock_db_repository.get_customer(customer_id)
        if row is None:
            raise CustomerNotFoundError(customer_id)
        return _customer_from_fixture_row(row)

    def get_by_source_unique_id(self, source_unique_id: str) -> Customer:
        raise CustomerNotFoundError(source_unique_id)

    def list_orders_summary(self, customer_id: str) -> list[OrderSummary]:
        orders = mock_db_repository.get_orders_by_customer_id(customer_id)
        return [
            OrderSummary(order_id=o["order_id"], order_status=None,
                         order_date=date.fromisoformat(o["order_date"]),
                         amount=Decimal(str(o["amount"])))
            for o in sorted(orders, key=lambda o: o["order_date"], reverse=True)
        ]

    def get_customer_risk_facts(self, customer_id: str) -> CustomerRiskFacts:
        customer_row = mock_db_repository.get_customer(customer_id)
        if customer_row is None:
            raise CustomerNotFoundError(customer_id)
        orders = mock_db_repository.get_orders_by_customer_id(customer_id)
        order_dates = [date.fromisoformat(o["order_date"]) for o in orders]
        return CustomerRiskFacts(
            customer_id=customer_row["customer_id"],
            vip_level=customer_row["vip_level"],
            canceled_order_count=customer_row.get("refund_history_count", 0),
            total_order_count=len(orders),
            completed_order_count=sum(1 for o in orders if o.get("delivered_date")),
            total_spend=sum((Decimal(str(o["amount"])) for o in orders), Decimal("0")),
            first_order_date=min(order_dates) if order_dates else None,
            last_order_date=max(order_dates) if order_dates else None,
        )


def _customer_from_fixture_row(row: dict) -> Customer:
    return Customer(
        customer_id=row["customer_id"], vip_level=row["vip_level"],
        register_date=date.fromisoformat(row["register_date"]) if row.get("register_date") else None,
        # the fixture's field is literally named refund_history_count, carried over from before this
        # review distinguished "canceled" from "refunded" -- it means the same thing
        # get_customer_risk_facts() below documents (a count of canceled orders, not confirmed refunds).
        canceled_order_count=row.get("refund_history_count", 0),
        source_unique_id=None,
    )


def get_customer_repository() -> CustomerRepository:
    """APP_DATA_BACKEND selects the implementation explicitly:
      'postgres' -> PostgresCustomerRepository (requires DATABASE_URL)
      'fixture'  -> FixtureCustomerRepository (default)
    No implicit fallback: an invalid combination (postgres requested but no
    DATABASE_URL) raises immediately rather than silently reading fixtures.
    """
    backend = os.getenv("APP_DATA_BACKEND", "fixture").strip().lower()
    if backend == "postgres":
        database_url = get_database_url()
        if not database_url:
            raise RuntimeError("APP_DATA_BACKEND=postgres requires DATABASE_URL to be set")
        return PostgresCustomerRepository(database_url)
    if backend == "fixture":
        return FixtureCustomerRepository()
    raise RuntimeError(f"unknown APP_DATA_BACKEND={backend!r}, expected 'postgres' or 'fixture'")
