from unittest.mock import patch

from src.backend.actions.withdraw_service_request_action import (
    withdraw_service_request_action_node,
)


@patch(
    "src.backend.actions.withdraw_service_request_action.withdraw_service_request",
    return_value={"request_id": "SR2026081001", "workflow_status": "Closed"},
)
def test_withdrawal_unbinds_closed_request_before_new_application(_withdraw):
    result = withdraw_service_request_action_node({
        "conversation_result": {
            "conversation_intent": "WithdrawServiceRequest",
            "request_id": "SR2026081001",
        },
        "conversation_context": {
            "authenticated_customer_id": "CUS0001",
            "active_order_id": "ORD2026080001",
            "active_request_id": "SR2026081001",
            "pending_action": "CreateServiceRequest",
            "pending_order_confirmation": "AfterSalesRequest",
            "pending_service_request": {"request_type": "Refund"},
        },
    })

    context = result["conversation_context"]
    assert context["active_order_id"] == "ORD2026080001"
    assert "active_request_id" not in context
    assert "pending_action" not in context
    assert "pending_order_confirmation" not in context
    assert "pending_service_request" not in context
    assert result["action_result"]["withdrawn_request_id"] == "SR2026081001"
    assert "request_id" not in result["action_result"]
