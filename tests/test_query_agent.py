from src.backend.agents.query_agent import _system_managed_stall_response


def test_system_managed_stall_does_not_claim_parcel_is_lost(monkeypatch):
    monkeypatch.setattr(
        "src.backend.agents.query_agent.get_logistics_tracking",
        lambda _order_id: {
            "carrier": "京东物流",
            "tracking_number": "JD123",
            "checkpoints": [
                {"time": "2026-08-09 11:00", "location": "西安转运中心"}
            ],
        },
    )
    response = _system_managed_stall_response(
        {
            "order_id": "ORD1",
            "estimated_delivery_date": "2026-08-08",
        },
        [
            {
                "carrier": "京东物流",
                "exception_type": "物流停滞超72小时",
                "resolved": False,
            }
        ],
    )

    assert response is not None
    assert "丢失" not in response
    assert "催一下" in response


def test_resolved_stall_is_not_treated_as_active(monkeypatch):
    monkeypatch.setattr(
        "src.backend.agents.query_agent.get_logistics_tracking",
        lambda _order_id: None,
    )
    response = _system_managed_stall_response(
        {"order_id": "ORD1"},
        [{"exception_type": "物流停滞超72小时", "resolved": True}],
    )

    assert response is None
