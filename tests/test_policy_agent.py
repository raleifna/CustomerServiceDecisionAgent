from src.backend.agents.policy_agent import policy_agent_node


def _signal(availability, value=None, reason=None, source_type=None):
    return {"availability": availability, "value": value, "reason": reason, "source_type": source_type}


def _base_state(**logistics_overrides):
    logistics_evidence = {
        "has_logistics_exception": _signal("AVAILABLE", True, None, "REAL"),
        "tracking_stalled_over_72h": _signal("AVAILABLE", False, None, "REAL"),
        "unresolved_tracking_stalled_over_72h": _signal("AVAILABLE", False, None, "REAL"),
        "stalled_in_transit_over_72h": _signal("AVAILABLE", False, None, "REAL"),
    }
    logistics_evidence.update(logistics_overrides)

    return {
        "service_request": {
            "request_id": "SR1", "request_type": "Refund", "request_reason": "未收到货",
            "requested_quantity": 1, "request_date": "2026-08-20",
        },
        "order_evidence": {
            "days_since_delivery": None, "is_customized": False, "is_delivered": False,
            "product_match": None, "quantity_match": None, "confirmed_missing_quantity": None,
            "remaining_eligible_quantity": 1, "ordered_quantity": 1, "amount": 100,
            "vip_level": "Bronze", "refund_history_count": 0, "payment_status": "Paid",
            "is_shipped": True, "is_in_transit_overdue": True,
        },
        "logistics_evidence": logistics_evidence,
        "evidence_review": {"requires_human_review": False},
        "customer": {"register_date": None},
    }


def test_available_true_tracking_stall_adds_shipping_002():
    state = _base_state(tracking_stalled_over_72h=_signal("AVAILABLE", True, None, "REAL"))
    result = policy_agent_node(state)
    assert "SHIPPING-002" in result["policy_evidence"]["candidate_policy_ids"]
    assert result["policy_evidence"]["missing_evidence"] == []


def test_available_false_tracking_stall_does_not_add_shipping_002():
    state = _base_state(tracking_stalled_over_72h=_signal("AVAILABLE", False, None, "REAL"))
    result = policy_agent_node(state)
    assert "SHIPPING-002" not in result["policy_evidence"]["candidate_policy_ids"]


def test_unavailable_tracking_stall_does_not_add_shipping_002_and_is_recorded_as_missing():
    state = _base_state(
        tracking_stalled_over_72h=_signal("UNAVAILABLE", None, "CONTINUOUS_TRACKING_NOT_PROVIDED"),
    )
    result = policy_agent_node(state)
    assert "SHIPPING-002" not in result["policy_evidence"]["candidate_policy_ids"]
    assert {"signal": "tracking_stalled_over_72h", "reason": "CONTINUOUS_TRACKING_NOT_PROVIDED"} \
        in result["policy_evidence"]["missing_evidence"]


def test_unavailable_stalled_in_transit_does_not_add_shipping_004_or_refund_007():
    state = _base_state(
        stalled_in_transit_over_72h=_signal("UNAVAILABLE", None, "TRANSIT_DETENTION_NOT_PROVIDED"),
    )
    state["service_request"]["request_reason"] = "商品损坏"
    state["order_evidence"]["days_since_delivery"] = 30
    result = policy_agent_node(state)
    ids = result["policy_evidence"]["candidate_policy_ids"]
    assert "SHIPPING-004" not in ids
    assert "REFUND-007" not in ids


def test_available_true_stalled_in_transit_adds_shipping_004():
    state = _base_state(stalled_in_transit_over_72h=_signal("AVAILABLE", True, None, "REAL"))
    result = policy_agent_node(state)
    assert "SHIPPING-004" in result["policy_evidence"]["candidate_policy_ids"]


# --- SHIPPING-007: the one rule with a negated condition ---

def test_shipping_007_fires_when_unresolved_stall_confirmed_false():
    state = _base_state(
        unresolved_tracking_stalled_over_72h=_signal("AVAILABLE", False, None, "REAL"),
    )
    result = policy_agent_node(state)
    assert "SHIPPING-007" in result["policy_evidence"]["candidate_policy_ids"]


def test_shipping_007_does_not_fire_when_unresolved_stall_is_unavailable():
    # The regression this whole slice exists to prevent: `not None` is True
    # in Python, so a naive negated read would have silently added
    # SHIPPING-007 here even though the stall status is genuinely unknown.
    state = _base_state(
        unresolved_tracking_stalled_over_72h=_signal(
            "UNAVAILABLE", None, "CONTINUOUS_TRACKING_NOT_PROVIDED",
        ),
    )
    result = policy_agent_node(state)
    assert "SHIPPING-007" not in result["policy_evidence"]["candidate_policy_ids"]
    assert any(
        m["signal"] == "unresolved_tracking_stalled_over_72h"
        for m in result["policy_evidence"]["missing_evidence"]
    )


def test_shipping_007_does_not_fire_when_unresolved_stall_confirmed_true():
    state = _base_state(
        unresolved_tracking_stalled_over_72h=_signal("AVAILABLE", True, None, "REAL"),
    )
    result = policy_agent_node(state)
    assert "SHIPPING-007" not in result["policy_evidence"]["candidate_policy_ids"]


# --- VIP-002 ---

def test_vip_002_requires_confirmed_unresolved_stall():
    state = _base_state(
        unresolved_tracking_stalled_over_72h=_signal("UNAVAILABLE", None, "CONTINUOUS_TRACKING_NOT_PROVIDED"),
    )
    state["order_evidence"]["vip_level"] = "Platinum"
    result = policy_agent_node(state)
    assert "VIP-002" not in result["policy_evidence"]["candidate_policy_ids"]


def test_vip_002_fires_with_confirmed_unresolved_stall():
    state = _base_state(
        unresolved_tracking_stalled_over_72h=_signal("AVAILABLE", True, None, "REAL"),
    )
    state["order_evidence"]["vip_level"] = "Platinum"
    result = policy_agent_node(state)
    assert "VIP-002" in result["policy_evidence"]["candidate_policy_ids"]
