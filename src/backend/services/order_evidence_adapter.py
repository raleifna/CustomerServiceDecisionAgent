"""Converts OrderRepository/CustomerRepository/ProductRepository dataclasses
into the plain dicts order_agent.py (and, downstream, policy_agent.py) read
via .get(...). Used by workflow.py's load_order_node/load_customer_node/
load_product_node so those loaders no longer import mock_db_repository.

Field-shape rules, load-bearing for order_agent.py's own None-handling to
stay correct:
  - Every date becomes an ISO string (order_agent.py calls
    date.fromisoformat() on these, exactly like it always did against the
    JSON fixture -- never leave a `date` object in the dict).
  - Every money value becomes a plain float (policy_agent.py's risk rules
    do `isinstance(amount, (int, float))`, which a Decimal fails).
  - promised_ship_date is dropped entirely, not passed through as None.
    It's a fixture-only concept (OrderRepository's Order dataclass never
    carried it -- this isn't a new omission from this migration), and nothing
    in the decision pipeline reads order_evidence.promised_ship_date --
    only query_agent.py's separate QueryToolProvider flow uses that field,
    and that migration already handles it (UNAVAILABLE/reason) on its own.
  - customer["refund_history_count"] is only ever populated for a FIXTURE
    customer (source_unique_id is None), where CustomerRepository's own
    canceled_order_count field already carries that exact pre-existing
    meaning under a renamed name (see customer_repository.py's
    _customer_from_fixture_row comment). A real V2 customer's
    canceled_order_count is a genuinely different, DERIVED fact
    (cancellation is not evidence of a refund -- see the customers table's
    own migration comment) and must never be aliased into this key for
    that backend -- the key is simply absent there. order_agent.py reads
    customer.get("refund_history_count") with an `or 0` fallback and
    combines it with a live-computed real count
    (get_completed_refund_count()), so an absent key on the V2 path
    resolves to exactly what a real customer's seed count should be: 0,
    letting the live-computed count carry the whole answer.
"""

from datetime import date
from decimal import Decimal
from typing import Any

from src.backend.repositories.customer_repository import Customer
from src.backend.repositories.order_repository import Order
from src.backend.repositories.product_repository import Product


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _num(value: Decimal | float | int | None) -> float | None:
    return float(value) if value is not None else None


def order_to_dict(order: Order) -> dict[str, Any]:
    return {
        "order_id": order.order_id,
        "customer_id": order.customer_id,
        "payment_status": order.payment_status,
        "shipped_date": _iso(order.shipped_date),
        "delivered_date": _iso(order.delivered_date),
        "estimated_delivery_date": _iso(order.estimated_delivery_date),
        "delivery_recipient_type": order.delivery_recipient_type,
        "amount": _num(order.amount),
    }


def customer_to_dict(customer: Customer) -> dict[str, Any]:
    result = {
        "customer_id": customer.customer_id,
        "vip_level": customer.vip_level,
        "register_date": _iso(customer.register_date),
    }
    if customer.source_unique_id is None:
        # Fixture customers only (source_unique_id is never None for a
        # real V2 customer -- it's populated from Olist's
        # customer_unique_id at import time): canceled_order_count on this
        # backend IS refund_history_count under a renamed field -- see
        # customer_repository.py's _customer_from_fixture_row comment
        # ("carried over from before this review distinguished canceled
        # from refunded... means the same thing"). Preserve that
        # pre-existing meaning exactly for the fixture/eval path. Do NOT
        # extend this aliasing to a real V2 customer, where
        # canceled_order_count is a genuinely different, DERIVED fact (see
        # the customers table's own migration comment) that must never be
        # reported as a refund count -- see module docstring.
        result["refund_history_count"] = customer.canceled_order_count
    return result


def product_to_dict(product: Product) -> dict[str, Any]:
    return {
        "product_id": product.product_id,
        "category": product.category,
        "price": _num(product.price),
        "is_fragile": product.is_fragile,
        "is_customized": product.is_customized,
    }
