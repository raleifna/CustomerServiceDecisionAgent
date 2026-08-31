from src.backend.agents.policy_agent import policy_agent_node


def _signal(availability, value=None, reason=None, source_type=None):
    return {"availability": availability, "value": value, "reason": reason, "source_type": source_type}


def _base_state(*, register_date, amount, refund_history_count=0, request_date="2026-08-20"):
    logistics_evidence = {
        "has_logistics_exception": _signal("AVAILABLE", False, None, "REAL"),
        "tracking_stalled_over_72h": _signal("AVAILABLE", False, None, "REAL"),
        "unresolved_tracking_stalled_over_72h": _signal("AVAILABLE", False, None, "REAL"),
        "stalled_in_transit_over_72h": _signal("AVAILABLE", False, None, "REAL"),
    }
    return {
        "service_request": {
            "request_id": "SR1", "request_type": "Refund", "request_reason": "质量问题",
            "requested_quantity": 1, "request_date": request_date,
        },
        "order_evidence": {
            "days_since_delivery": 2, "is_customized": False, "is_delivered": True,
            "product_match": None, "quantity_match": None, "confirmed_missing_quantity": None,
            "remaining_eligible_quantity": 1, "ordered_quantity": 1, "amount": amount,
            "vip_level": "Bronze", "refund_history_count": refund_history_count, "payment_status": "Paid",
            "is_shipped": True, "is_in_transit_overdue": False,
        },
        "logistics_evidence": logistics_evidence,
        "evidence_review": {"requires_human_review": False},
        "customer": {"register_date": register_date},
    }


# --- known register_date: unchanged, real computation ---

def test_new_account_with_known_register_date_triggers_risk_003():
    # request_date=2026-08-20, register_date=2026-08-01 -> 19 days old, within 30
    state = _base_state(register_date="2026-08-01", amount=6000)
    result = policy_agent_node(state)
    ids = result["policy_evidence"]["candidate_policy_ids"]
    assert "RISK-003" in ids
    assert result["policy_evidence"]["missing_evidence"] == []


def test_old_account_with_known_register_date_does_not_trigger_risk_003():
    state = _base_state(register_date="2020-01-01", amount=6000)
    result = policy_agent_node(state)
    assert "RISK-003" not in result["policy_evidence"]["candidate_policy_ids"]
    assert result["policy_evidence"]["missing_evidence"] == []


# --- unknown register_date (V2/Olist customers): must not silently skip
# when the other two RISK-003 preconditions hold ---

def test_unknown_register_date_with_high_value_and_zero_refunds_still_triggers_risk_003():
    state = _base_state(register_date=None, amount=6000, refund_history_count=0)
    result = policy_agent_node(state)
    ids = result["policy_evidence"]["candidate_policy_ids"]
    assert "RISK-003" in ids
    assert any(
        m["signal"] == "customer_register_date" and m["reason"] == "REGISTRATION_DATE_NOT_PROVIDED"
        for m in result["policy_evidence"]["missing_evidence"]
    )


def test_unknown_register_date_reason_text_does_not_assert_new_account_as_fact():
    state = _base_state(register_date=None, amount=6000, refund_history_count=0)
    result = policy_agent_node(state)
    reasons = result["policy_evidence"]["selection_reasons"]["RISK-003"]
    assert any("无法确认" in r for r in reasons)
    assert not any(r == "新注册账号在30天内申请大额退款" for r in reasons)


def test_unknown_register_date_below_high_value_threshold_does_not_trigger_risk_003():
    state = _base_state(register_date=None, amount=100, refund_history_count=0)
    result = policy_agent_node(state)
    assert "RISK-003" not in result["policy_evidence"]["candidate_policy_ids"]
    # still recorded as missing evidence even though it didn't need to fire
    assert any(m["signal"] == "customer_register_date" for m in result["policy_evidence"]["missing_evidence"])


def test_unknown_register_date_with_nonzero_refund_history_does_not_trigger_risk_003():
    state = _base_state(register_date=None, amount=6000, refund_history_count=2)
    result = policy_agent_node(state)
    assert "RISK-003" not in result["policy_evidence"]["candidate_policy_ids"]
