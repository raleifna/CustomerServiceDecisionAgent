import os

from src.backend.agents.order_agent import order_agent_node


def _base_state(**order_item_overrides):
    order_item = {
        "order_item_id": "OI1", "sku_id": "SKU1", "unit_price": 100.0,
        "product_id": "PRO1", "shipped_product_id": None,
        "quantity": 1, "shipped_quantity": None, "paid_amount": 100.0,
    }
    order_item.update(order_item_overrides)

    return {
        "order": {
            "order_id": "ORD1", "shipped_date": None, "delivered_date": None,
            "estimated_delivery_date": "2026-01-10", "delivery_recipient_type": None,
            "payment_status": "Paid",
        },
        "service_request": {
            "request_id": "SR1", "request_reason": "质量问题", "request_date": "2026-01-15",
        },
        "order_item": order_item,
        "product": {"is_fragile": False, "is_customized": False},
        "customer": {"vip_level": "Bronze", "customer_id": "CUS1"},
    }


def test_promised_ship_date_is_absent_from_output():
    result = order_agent_node(_base_state())
    assert "promised_ship_date" not in result["order_evidence"]


def test_not_shipped_gives_not_applicable_fulfillment_tracking():
    result = order_agent_node(_base_state())
    tracking = result["order_evidence"]["fulfillment_tracking"]
    assert tracking == {"availability": "NOT_APPLICABLE", "reason": "NOT_YET_SHIPPED"}
    assert result["order_evidence"]["product_match"] is None


def test_shipped_with_no_fulfillment_data_gives_unavailable_fulfillment_tracking():
    state = _base_state()
    state["order"]["shipped_date"] = "2026-01-05"  # shipped...
    # ...but shipped_product_id/shipped_quantity stay None -- V2/Olist shape
    result = order_agent_node(state)
    tracking = result["order_evidence"]["fulfillment_tracking"]
    assert tracking == {"availability": "UNAVAILABLE", "reason": "FULFILLMENT_TRACKING_NOT_PROVIDED"}
    assert result["order_evidence"]["product_match"] is None
    assert result["order_evidence"]["is_shipped"] is True


def test_shipped_with_fulfillment_data_gives_available_and_real_match():
    state = _base_state(shipped_product_id="PRO1", shipped_quantity=1)
    state["order"]["shipped_date"] = "2026-01-05"
    result = order_agent_node(state)
    evidence = result["order_evidence"]
    assert evidence["fulfillment_tracking"] == {"availability": "AVAILABLE", "reason": None}
    assert evidence["product_match"] is True
    assert evidence["quantity_match"] is True


def test_shipped_with_fulfillment_mismatch_is_a_real_false_not_none():
    state = _base_state(shipped_product_id="PRO-DIFFERENT", shipped_quantity=1)
    state["order"]["shipped_date"] = "2026-01-05"
    result = order_agent_node(state)
    evidence = result["order_evidence"]
    assert evidence["fulfillment_tracking"]["availability"] == "AVAILABLE"
    assert evidence["product_match"] is False


def test_fixture_style_refund_history_count_still_reads_through(monkeypatch):
    monkeypatch.setattr(
        "src.backend.agents.order_agent.get_completed_refund_count", lambda customer_id: 0,
    )
    state = _base_state()
    state["customer"]["refund_history_count"] = 12  # fixture-shaped customer dict
    result = order_agent_node(state)
    assert result["order_evidence"]["refund_history_count"] == 12


def test_v2_style_customer_without_refund_history_count_key_uses_live_count(monkeypatch):
    monkeypatch.setattr(
        "src.backend.agents.order_agent.get_completed_refund_count", lambda customer_id: 3,
    )
    state = _base_state()
    # no "refund_history_count" key at all -- matches order_evidence_adapter's
    # V2 customer_to_dict() output
    result = order_agent_node(state)
    assert result["order_evidence"]["refund_history_count"] == 3
