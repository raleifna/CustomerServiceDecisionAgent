from datetime import date
from decimal import Decimal

import pytest

from src.backend.repositories.order_repository import FixtureOrderRepository, OrderItem, OrderNotFoundError
from src.backend.repositories.payment_repository import FixturePaymentRepository
from src.backend.repositories.product_repository import FixtureProductRepository
from src.backend.repositories.shipment_repository import FixtureShipmentRepository
from src.backend.services.order_context_service import (
    ConsistencyReport,
    OrderContext,
    OrderContextIntegrityError,
    OrderContextNotFoundError,
    OrderContextService,
)


@pytest.fixture
def service():
    return OrderContextService(
        order_repository=FixtureOrderRepository(),
        product_repository=FixtureProductRepository(),
        shipment_repository=FixtureShipmentRepository(),
        payment_repository=FixturePaymentRepository(),
    )


def test_get_order_context_returns_full_context_for_multi_item_order(service):
    ctx = service.get_order_context("ORD2026080027", as_of_date=date(2026, 8, 20))
    assert isinstance(ctx, OrderContext)
    assert ctx.schema_version == "v2"
    assert ctx.order.order_id == "ORD2026080027"
    assert len(ctx.items) == 2
    for item_ctx in ctx.items:
        assert item_ctx.product is not None
        assert item_ctx.product.product_id == item_ctx.order_item.product_id


def test_get_order_context_raises_not_found_for_unknown_order(service):
    with pytest.raises(OrderContextNotFoundError):
        service.get_order_context("ORD9999-DOES-NOT-EXIST", as_of_date=date(2026, 8, 20))


def test_missing_payments_is_a_warning_not_an_error(service):
    ctx = service.get_order_context("ORD2026080001", as_of_date=date(2026, 8, 20))
    assert ctx.payments == []
    assert ctx.payment_facts is None
    codes = {w.code for w in ctx.warnings}
    assert "NO_PAYMENT_RECORDS" in codes


def test_consistency_report_reconciles_order_items_against_order_total(service):
    ctx = service.get_order_context("ORD2026080027", as_of_date=date(2026, 8, 20))
    c = ctx.consistency
    assert isinstance(c, ConsistencyReport)
    assert c.order_items_total == Decimal("1098")
    assert c.order_total == Decimal("1098.0")
    assert c.order_amount_reconciled is True
    assert c.payment_total is None            # no payments tracked on fixture backend
    assert c.payment_amount_reconciled is None  # not False -- absence isn't a mismatch
    assert c.missing_product_ids == []


def test_consistency_report_has_no_refund_or_risk_fields():
    field_names = set(ConsistencyReport.__dataclass_fields__)
    for forbidden in ("refund_eligible", "risk_level", "recommended_action", "matched_rule"):
        assert forbidden not in field_names


def test_order_context_has_no_refund_or_risk_fields():
    field_names = set(OrderContext.__dataclass_fields__)
    for forbidden in ("refund_eligible", "risk_level", "recommended_action", "matched_rule", "decision"):
        assert forbidden not in field_names


def test_source_summary_reflects_fixture_backend(service):
    ctx = service.get_order_context("ORD2026080001", as_of_date=date(2026, 8, 20))
    assert "fixture" in ctx.source_summary.order_facts.lower()
    assert "fixture" in ctx.source_summary.product_assignment.lower()


def test_as_of_date_changes_shipment_facts_overdue_status(service):
    # ORD2026080023: shipped 08-13, undelivered, estimated 2026-08-18
    ctx_before = service.get_order_context("ORD2026080023", as_of_date=date(2026, 8, 15))
    ctx_after = service.get_order_context("ORD2026080023", as_of_date=date(2026, 8, 25))
    assert ctx_before.shipment_facts.is_overdue is False
    assert ctx_after.shipment_facts.is_overdue is True
    assert ctx_before.as_of_date == date(2026, 8, 15)
    assert ctx_after.as_of_date == date(2026, 8, 25)


def test_integrity_error_when_an_order_item_product_is_missing(service, monkeypatch):
    # Force a broken order_item referencing a product_id that doesn't
    # exist -- exercised via monkeypatching get_order_items since the real
    # fixture data has no such broken row to test against.
    from src.backend.repositories import order_repository as order_repo_module

    real_get_items = FixtureOrderRepository.get_order_items

    def broken_get_items(self, order_id):
        items = real_get_items(self, order_id)
        broken = OrderItem(
            order_item_id="OI-BROKEN", order_id=order_id, product_id="PRO-DOES-NOT-EXIST",
            sku_id="SKU-BROKEN", sku_name="broken", unit_price=Decimal("1"), quantity=1,
            discount_amount=Decimal("0"), paid_amount=Decimal("1"),
            shipped_product_id=None, shipped_quantity=None,
        )
        return items + [broken]

    monkeypatch.setattr(order_repo_module.FixtureOrderRepository, "get_order_items", broken_get_items)

    with pytest.raises(OrderContextIntegrityError) as exc_info:
        service.get_order_context("ORD2026080001", as_of_date=date(2026, 8, 20))
    assert exc_info.value.missing_product_ids == ["PRO-DOES-NOT-EXIST"]
