from src.backend.agents.conversation_router_agent import conversation_router_node


def test_status_query_overrides_stale_service_request_draft():
    result = conversation_router_node({
        "user_message": "我的售后进度如何了",
        "conversation_context": {
            "authenticated_customer_id": "CUS0001",
            "active_order_id": "ORD2026080001",
            "active_request_id": "SR2026081001",
            "pending_action": "CreateServiceRequest",
            "pending_service_request": {"request_type": None},
            "last_intent": "AfterSalesRequest",
        },
    })

    assert result["conversation_result"]["conversation_intent"] == "AfterSalesStatusQuery"
    assert result["conversation_result"]["request_id"] == "SR2026081001"


def test_withdrawal_overrides_stale_service_request_draft():
    result = conversation_router_node({
        "user_message": "算了，我不退了",
        "conversation_context": {
            "authenticated_customer_id": "CUS0001",
            "active_order_id": "ORD2026080001",
            "active_request_id": "SR2026081001",
            "pending_action": "CreateServiceRequest",
            "pending_service_request": {"request_type": "Refund"},
        },
    })

    assert result["conversation_result"]["conversation_intent"] == "WithdrawServiceRequest"


def test_colloquial_refund_request_with_greeting_uses_selected_order():
    result = conversation_router_node({
        "user_message": "你好，我这个有问题，我想退了",
        "conversation_context": {
            "authenticated_customer_id": "CUS0001",
            "active_order_id": "ORD2026080001",
            "order_context_confirmed": False,
        },
    })

    assert result["conversation_result"]["conversation_intent"] == "AfterSalesRequest"
    assert result["conversation_result"]["order_id"] == "ORD2026080001"


def test_reapplication_confirmation_starts_a_fresh_request_flow():
    result = conversation_router_node({
        "user_message": "是的，重新申请",
        "conversation_context": {
            "authenticated_customer_id": "CUS0001",
            "active_order_id": "ORD2026080001",
            "pending_reapplication_confirmation": True,
            "pending_action": "ConfirmReapplication",
        },
    })

    assert result["conversation_result"]["conversation_intent"] == "AfterSalesRequest"
    assert result["conversation_result"]["request_id"] is None
    assert result["conversation_context"]["reapplication_confirmed_for_order"] == "ORD2026080001"
    assert result["conversation_context"]["pending_action"] == "CreateServiceRequest"
