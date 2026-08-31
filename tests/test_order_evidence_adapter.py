from datetime import date
from decimal import Decimal

from src.backend.repositories.customer_repository import Customer
from src.backend.repositories.order_repository import Order
from src.backend.repositories.product_repository import Product
from src.backend.services.order_evidence_adapter import (
    customer_to_dict,
    order_to_dict,
    product_to_dict,
)


def test_order_to_dict_converts_dates_to_iso_strings_and_amount_to_float():
    order = Order(
        order_id="ORD1", customer_id="CUS1", order_status="delivered", payment_status="Paid",
        refund_status=None, order_date=date(2026, 1, 1), shipped_date=date(2026, 1, 2),
        delivered_date=date(2026, 1, 5), estimated_delivery_date=date(2026, 1, 10),
        delivery_recipient_type="Self", amount=Decimal("199.99"), rounding_adjustment=Decimal("0"),
    )
    result = order_to_dict(order)
    assert result["shipped_date"] == "2026-01-02"
    assert result["delivered_date"] == "2026-01-05"
    assert result["estimated_delivery_date"] == "2026-01-10"
    assert result["amount"] == 199.99
    assert isinstance(result["amount"], float)
    assert "promised_ship_date" not in result  # dropped, not passed through as None


def test_order_to_dict_handles_null_dates():
    order = Order(
        order_id="ORD1", customer_id="CUS1", order_status="shipped", payment_status="Paid",
        refund_status=None, order_date=date(2026, 1, 1), shipped_date=None,
        delivered_date=None, estimated_delivery_date=date(2026, 1, 10),
        delivery_recipient_type=None, amount=Decimal("50"), rounding_adjustment=Decimal("0"),
    )
    result = order_to_dict(order)
    assert result["shipped_date"] is None
    assert result["delivered_date"] is None


def test_fixture_customer_aliases_canceled_order_count_as_refund_history_count():
    # source_unique_id=None => fixture customer (see Customer's own field
    # docstring). On this backend canceled_order_count IS the legacy
    # refund_history_count under a renamed field -- must be preserved.
    customer = Customer(
        customer_id="CUS1", vip_level="Gold", register_date=date(2020, 1, 1),
        canceled_order_count=12, source_unique_id=None,
    )
    result = customer_to_dict(customer)
    assert result["refund_history_count"] == 12


def test_v2_customer_never_carries_refund_history_count():
    # source_unique_id set => a real V2 customer. canceled_order_count is a
    # genuinely different fact there and must never be aliased.
    customer = Customer(
        customer_id="CUS-RW-00001", vip_level="Gold", register_date=None,
        canceled_order_count=5, source_unique_id="olist-abc123",
    )
    result = customer_to_dict(customer)
    assert "refund_history_count" not in result
    assert result["vip_level"] == "Gold"


def test_product_to_dict_converts_price_to_float():
    product = Product(product_id="PRO1", category="electronics", price=Decimal("59.90"), is_fragile=True, is_customized=False)
    result = product_to_dict(product)
    assert result["price"] == 59.9
    assert isinstance(result["price"], float)
    assert result["is_fragile"] is True
