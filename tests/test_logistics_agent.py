from src.backend.agents.logistics_agent import logistics_agent_node
from src.backend.services.logistics_service import AVAILABLE, UNAVAILABLE


def _signal(availability, value=None, reason=None, source_type=None):
    return {"availability": availability, "value": value, "reason": reason, "source_type": source_type}


def test_missing_logistics_or_order_reports_an_error():
    assert "errors" in logistics_agent_node({"logistics": None, "order": {"order_id": "ORD1"}})
    assert "errors" in logistics_agent_node({"logistics": {"exceptions": []}, "order": None})


def test_fixture_shaped_signals_pass_through_unchanged():
    state = {
        "order": {"order_id": "ORD1"},
        "logistics": {
            "order_id": "ORD1",
            "exceptions": [
                {"carrier": "京东物流", "exception_type": "物流停滞超72小时",
                 "exception_recorded_date": "2026-08-09", "resolved": False},
            ],
            "has_logistics_exception": _signal(AVAILABLE, True, None, "REAL"),
            "has_unresolved_exception": _signal(AVAILABLE, True, None, "REAL"),
            "delivery_overdue": _signal(AVAILABLE, True, None, "DERIVED"),
            "tracking_stalled_over_72h": _signal(AVAILABLE, True, None, "REAL"),
            "unresolved_tracking_stalled_over_72h": _signal(AVAILABLE, True, None, "REAL"),
            "stalled_in_transit_over_72h": _signal(AVAILABLE, False, None, "REAL"),
        },
    }

    result = logistics_agent_node(state)
    evidence = result["logistics_evidence"]

    assert evidence["order_id"] == "ORD1"
    assert evidence["exception_count"] == 1
    assert evidence["exception_types"] == ["物流停滞超72小时"]
    assert evidence["unresolved_exception_types"] == ["物流停滞超72小时"]
    assert evidence["tracking_stalled_over_72h"] == _signal(AVAILABLE, True, None, "REAL")
    assert evidence["stalled_in_transit_over_72h"]["value"] is False


def test_postgres_shaped_unavailable_signals_pass_through_as_unavailable_not_false():
    state = {
        "order": {"order_id": "ORD-RW-00001"},
        "logistics": {
            "order_id": "ORD-RW-00001",
            "exceptions": [
                {"carrier": None, "exception_type": "超过预计送达时间仍未签收",
                 "exception_recorded_date": "2026-08-09", "resolved": False},
            ],
            "has_logistics_exception": _signal(AVAILABLE, True, None, "DERIVED"),
            "has_unresolved_exception": _signal(AVAILABLE, True, None, "DERIVED"),
            "delivery_overdue": _signal(AVAILABLE, True, None, "DERIVED"),
            "tracking_stalled_over_72h": _signal(UNAVAILABLE, None, "CONTINUOUS_TRACKING_NOT_PROVIDED"),
            "unresolved_tracking_stalled_over_72h": _signal(UNAVAILABLE, None, "CONTINUOUS_TRACKING_NOT_PROVIDED"),
            "stalled_in_transit_over_72h": _signal(UNAVAILABLE, None, "TRANSIT_DETENTION_NOT_PROVIDED"),
        },
    }

    result = logistics_agent_node(state)
    evidence = result["logistics_evidence"]

    assert evidence["exception_types"] == ["超过预计送达时间仍未签收"]
    assert evidence["tracking_stalled_over_72h"]["availability"] == UNAVAILABLE
    assert evidence["tracking_stalled_over_72h"]["value"] is None  # never coerced to False
    assert evidence["stalled_in_transit_over_72h"]["availability"] == UNAVAILABLE
    assert evidence["delivery_overdue"]["availability"] == AVAILABLE
    assert evidence["delivery_overdue"]["value"] is True
