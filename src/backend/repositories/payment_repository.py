"""Payment read repository: same Protocol + two-implementation +
explicit-APP_DATA_BACKEND pattern as the sibling repositories.

Scope boundary this module exists to enforce -- do not blur it when wiring
callers:
  - Owns `payments` only. Never queries `orders`, `shipments`, or `refunds`.
  - Does NOT judge refund status. Whether a canceled order's payment was
    actually refunded is `refunds.refund_status`'s job (a separate table,
    read through a future RefundRepository/OrderContextService) -- this
    module never infers "refunded" from order_status='canceled', and never
    infers "payment succeeded" merely from a payments row existing, because
    the data model here has no reliable payment-success status field to
    check in the first place (see get_payment_facts docstring).
  - `payment_reference_date` is NOT a real payment-channel timestamp.
    Olist's payments dataset has no timestamp field at all; this system
    reuses the order's purchase date as a reference point (source_type
    DERIVED in data_provenance). Never call this "when the payment was
    made" in any caller-facing text.
  - `payment_installments` is passed through verbatim and must never be
    read as "number of payments" -- a single payment_installments=8 row is
    one payment split into 8 billing installments by the card issuer, not
    eight separate payment events. The number of actual payment ROWS for an
    order (list_by_order_id length) is the only correct "how many payments"
    answer, and that's a materially different number from any given row's
    payment_installments.
"""

import os
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol

from src.backend.repositories.postgres_repository import get_database_url


class PaymentNotFoundError(LookupError):
    """The given payment_id doesn't exist. Distinct from
    NoPaymentsForOrderError: this means the id itself is wrong."""


class NoPaymentsForOrderError(LookupError):
    """The given order_id has no payment rows on record. Distinct from
    PaymentNotFoundError: the order_id may be perfectly valid, it simply
    has no payments tracked here (e.g. the fixture backend, which has no
    payments concept at all -- see FixturePaymentRepository)."""


@dataclass(frozen=True)
class Payment:
    payment_id: str
    order_id: str
    payment_sequential: int | None   # real Olist field; stable ordering key for split payments
    amount: Decimal
    currency: str
    payment_method: str | None       # passed through verbatim, e.g. '信用卡'/'银行转账'/'代金券'/'借记卡'
    payment_installments: int | None  # billing installment count, NOT a payment count -- see module docstring
    payment_reference_date: date | None  # DERIVED, not an observed payment timestamp -- see module docstring


@dataclass(frozen=True)
class PaymentFacts:
    """Facts only -- no refund-status judgment (that's refunds.refund_status,
    read elsewhere) and no payment-success inference from row presence
    (this data model has no reliable success/failure status field on
    payments at all; a row here means "a payment record exists for this
    amount", nothing stronger)."""
    order_id: str
    payment_count: int
    total_amount: Decimal
    payment_methods: list[str]         # in payment_sequential order, duplicates kept
    has_split_payment: bool            # more than one payment row for this order


class PaymentRepository(Protocol):
    def get_by_id(self, payment_id: str) -> Payment: ...
    def list_by_order_id(self, order_id: str) -> list[Payment]: ...
    def get_payment_facts(self, order_id: str) -> PaymentFacts: ...


class PostgresPaymentRepository:
    """Reads the real `payments` table only. Lets any psycopg connection/
    query error propagate unmodified -- a database outage must be visible,
    not masked by a fallback to fixture data."""

    def __init__(self, database_url: str):
        self._database_url = database_url

    def _connect(self):
        import psycopg
        from psycopg.rows import dict_row
        return psycopg.connect(self._database_url, row_factory=dict_row)

    def get_by_id(self, payment_id: str) -> Payment:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT payment_id, order_id, payment_sequential, amount, currency, "
                "payment_method, payment_installments, payment_reference_date "
                "FROM payments WHERE payment_id = %s",
                (payment_id,),
            )
            row = cur.fetchone()
        if row is None:
            raise PaymentNotFoundError(payment_id)
        return _payment_from_row(row)

    def list_by_order_id(self, order_id: str) -> list[Payment]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT payment_id, order_id, payment_sequential, amount, currency, "
                "payment_method, payment_installments, payment_reference_date "
                "FROM payments WHERE order_id = %s "
                "ORDER BY payment_sequential NULLS LAST, payment_id",
                (order_id,),
            )
            rows = cur.fetchall()
        if not rows:
            raise NoPaymentsForOrderError(order_id)
        return [_payment_from_row(r) for r in rows]

    def get_payment_facts(self, order_id: str) -> PaymentFacts:
        payments = self.list_by_order_id(order_id)
        return _facts_from_payments(order_id, payments)


def _payment_from_row(row: dict) -> Payment:
    return Payment(
        payment_id=row["payment_id"], order_id=row["order_id"],
        payment_sequential=row["payment_sequential"], amount=Decimal(row["amount"]),
        currency=row["currency"], payment_method=row["payment_method"],
        payment_installments=row["payment_installments"],
        payment_reference_date=row["payment_reference_date"],
    )


def _facts_from_payments(order_id: str, payments: list[Payment]) -> PaymentFacts:
    return PaymentFacts(
        order_id=order_id, payment_count=len(payments),
        total_amount=sum((p.amount for p in payments), Decimal("0")),
        payment_methods=[p.payment_method for p in payments if p.payment_method is not None],
        has_split_payment=len(payments) > 1,
    )


class FixturePaymentRepository:
    """The legacy fixture schema has no `payments` table or concept at all
    -- every fixture order's payment_status is just a flat 'Paid' string on
    the order row itself, with no per-payment breakdown, no
    payment_sequential, no payment_method. There is nothing to derive a
    Payment row from (unlike ShipmentRepository, where orders.json's own
    date fields give a real basis to derive from), so every lookup here
    raises NoPaymentsForOrderError -- this is a genuine, documented backend
    gap, not a bug.
    """

    def get_by_id(self, payment_id: str) -> Payment:
        raise PaymentNotFoundError(payment_id)

    def list_by_order_id(self, order_id: str) -> list[Payment]:
        raise NoPaymentsForOrderError(order_id)

    def get_payment_facts(self, order_id: str) -> PaymentFacts:
        raise NoPaymentsForOrderError(order_id)


def get_payment_repository() -> PaymentRepository:
    """Same explicit-selection contract as the sibling repositories: no
    implicit fallback between backends."""
    backend = os.getenv("APP_DATA_BACKEND", "fixture").strip().lower()
    if backend == "postgres":
        database_url = get_database_url()
        if not database_url:
            raise RuntimeError("APP_DATA_BACKEND=postgres requires DATABASE_URL to be set")
        return PostgresPaymentRepository(database_url)
    if backend == "fixture":
        return FixturePaymentRepository()
    raise RuntimeError(f"unknown APP_DATA_BACKEND={backend!r}, expected 'postgres' or 'fixture'")
