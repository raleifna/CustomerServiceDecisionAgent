from unittest.mock import patch

from src.backend.actions.service_request_action import service_request_action_node


@patch(
    "src.backend.actions.service_request_action.get_recent_submission_stats",
    return_value={"submission_count": 1, "latest_was_withdrawn": True},
)
@patch("src.backend.actions.service_request_action.get_order")
@patch("src.backend.actions.service_request_action._extract_details")
def test_withdrawn_request_requires_explicit_reapplication_confirmation(
    extract_details,
    get_order,
    _stats,
):
    extract_details.return_value = {
        "request_type": "Refund",
        "request_reason": None,
        "requested_quantity": None,
    }
    get_order.return_value = {
        "order_id": "ORD2026080001",
        "customer_id": "CUS0001",
        "product_id": "PRO0001",
        "shipped_date": "2026-08-02",
        "delivered_date": "2026-08-04",
    }

    result = service_request_action_node({
        "user_message": "我要退",
        "conversation_result": {
            "conversation_intent": "AfterSalesRequest",
            "order_id": "ORD2026080001",
        },
        "conversation_context": {
            "authenticated_customer_id": "CUS0001",
            "active_order_id": "ORD2026080001",
            "order_context_confirmed": True,
        },
    })

    assert "是否要重新发起一笔新的售后申请" in result["customer_response"]
    assert result["conversation_context"]["pending_reapplication_confirmation"] is True
    assert "pending_service_request" not in result["conversation_context"]
