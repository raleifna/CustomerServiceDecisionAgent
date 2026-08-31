from src.backend.agents import query_agent
from src.backend.agents.query_agent import _system_managed_stall_response, query_agent_node
from src.backend.services.tool_errors import DATABASE_UNAVAILABLE, DATA_INTEGRITY_ERROR, ToolError


def test_system_managed_stall_does_not_claim_parcel_is_lost():
    order_status = {"order_id": "ORD1"}
    logistics_status = {
        "unresolved_stall": True,
        "estimated_delivery_at": "2026-08-08",
        "exceptions": [
            {"carrier": "京东物流", "exception_type": "物流停滞超72小时", "resolved": False}
        ],
        "tracking": {
            "availability": "AVAILABLE",
            "carrier": "京东物流",
            "tracking_number": "JD123",
            "checkpoints": [{"time": "2026-08-09 11:00", "location": "西安转运中心"}],
        },
    }

    response = _system_managed_stall_response(order_status, logistics_status, as_of_iso="2026-08-20")

    assert response is not None
    assert "丢失" not in response
    assert "催一下" in response


def test_resolved_stall_is_not_treated_as_active():
    order_status = {"order_id": "ORD1"}
    logistics_status = {
        "unresolved_stall": False,
        "estimated_delivery_at": None,
        "exceptions": [{"exception_type": "物流停滞超72小时", "resolved": True}],
        "tracking": {"availability": "UNAVAILABLE", "carrier": None, "tracking_number": None, "checkpoints": []},
    }

    response = _system_managed_stall_response(order_status, logistics_status, as_of_iso="2026-08-20")

    assert response is None


def test_stall_response_with_unavailable_tracking_omits_location_text():
    # tracking.availability == UNAVAILABLE (Postgres backend) -- the stall
    # message must not fabricate a checkpoint location it doesn't have.
    order_status = {"order_id": "ORD-RW-00001"}
    logistics_status = {
        "unresolved_stall": True,
        "estimated_delivery_at": "2026-08-08",
        "exceptions": [{"carrier": "第三方物流", "exception_type": "超过预计送达时间仍未签收", "resolved": False}],
        "tracking": {"availability": "UNAVAILABLE", "carrier": None, "tracking_number": None, "checkpoints": []},
    }

    response = _system_managed_stall_response(order_status, logistics_status, as_of_iso="2026-08-20")

    assert response is not None
    assert "最新一条轨迹" not in response
    assert "丢失" not in response


class _StubProviderRaisingToolError:
    def __init__(self, exc: ToolError):
        self._exc = exc

    def list_orders_for_customer(self, customer_id):
        raise self._exc

    def get_order_status(self, order_id, *, as_of_date):
        raise self._exc

    def get_logistics_status(self, order_id, *, as_of_date):
        raise self._exc


def _minimal_state(order_id="ORD1"):
    return {
        "conversation_result": {"order_id": order_id},
        "conversation_context": {},
        "dialogue_history": [],
        "user_message": "订单到哪了",
    }


def test_query_agent_node_reports_retryable_message_for_database_unavailable(monkeypatch):
    exc = ToolError(DATABASE_UNAVAILABLE, "The database is temporarily unavailable.")
    monkeypatch.setattr(query_agent, "get_query_tool_provider", lambda: _StubProviderRaisingToolError(exc))

    result = query_agent_node(_minimal_state())

    assert "稍后再试" in result["customer_response"]
    assert "The database is temporarily unavailable" not in result["customer_response"]


def test_query_agent_node_reports_generic_message_for_non_retryable_tool_error(monkeypatch):
    exc = ToolError(DATA_INTEGRITY_ERROR, "Order data failed an internal consistency check.")
    monkeypatch.setattr(query_agent, "get_query_tool_provider", lambda: _StubProviderRaisingToolError(exc))

    result = query_agent_node(_minimal_state())

    assert "人工客服" in result["customer_response"]
    assert "consistency check" not in result["customer_response"]
