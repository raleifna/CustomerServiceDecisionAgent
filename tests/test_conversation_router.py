import src.backend.agents.conversation_router_agent as conversation_router_agent
from src.backend.agents.conversation_router_agent import (
    _OWNERSHIP_UNVERIFIED_RESPONSE,
    conversation_router_node,
)
from src.backend.services.order_ownership_service import DENIED, OwnershipResult
from src.backend.services.tool_errors import DATABASE_UNAVAILABLE, ToolError


class _DeniedService:
    def verify_ownership(self, order_id, customer_id):
        return OwnershipResult(DENIED)


def test_llm_withdraw_misclassification_falls_back_to_after_sales_request(monkeypatch):
    # "给我退掉吧" has no active_request_id to withdraw and isn't in the
    # curated withdrawal keyword list, so it reaches the LLM classifier. A
    # real misclassification observed in manual testing: the LLM read the
    # colloquial "退掉" as withdrawal language and returned
    # WithdrawServiceRequest, which dead-ended the turn on "没有可撤销的售后
    # 申请" instead of ever creating the refund request the customer asked
    # for. Simulate that misclassification here and assert the guard
    # recovers it.
    monkeypatch.setattr(
        conversation_router_agent,
        "chat_json",
        lambda *args, **kwargs: {"conversation_intent": "WithdrawServiceRequest"},
    )

    result = conversation_router_node({
        "user_message": "这个一直没发货给我退掉吧",
        "conversation_context": {
            "authenticated_customer_id": "CUS0001",
            "active_order_id": "ORD2026080001",
        },
    })

    assert result["conversation_result"]["conversation_intent"] == "AfterSalesRequest"


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


def test_v2_shaped_order_id_still_goes_through_ownership_check(monkeypatch):
    # Recognizing the new ORD-RW-##### shape must not bypass the same
    # authorization guard the old ORD########## shape goes through --
    # format recognition and ownership verification are separate steps.
    monkeypatch.setattr(
        conversation_router_agent, "get_order_ownership_service", lambda: _DeniedService(),
    )

    result = conversation_router_node({
        "user_message": "订单 ORD-RW-00001 什么时候到",
        "conversation_context": {"authenticated_customer_id": "CUS-RW-00001"},
    })

    assert result["conversation_result"]["status"] == "Rejected"
    assert result["conversation_result"]["order_id"] is None
    assert result["customer_response"] == _OWNERSHIP_UNVERIFIED_RESPONSE


def test_ownership_denied_gives_the_uniform_response_and_resets_context(monkeypatch):
    monkeypatch.setattr(conversation_router_agent, "get_order_ownership_service", lambda: _DeniedService())

    result = conversation_router_node({
        "user_message": "这个订单 ORD2026080099 什么时候发货",
        "conversation_context": {"authenticated_customer_id": "CUS0001"},
    })

    assert result["conversation_result"]["status"] == "Rejected"
    assert result["conversation_result"]["order_id"] is None
    assert result["customer_response"] == _OWNERSHIP_UNVERIFIED_RESPONSE
    assert "active_order_id" not in result["conversation_context"]


def test_ownership_check_infra_failure_fails_closed_with_the_same_uniform_response(monkeypatch):
    # A DATABASE_UNAVAILABLE ToolError must be treated exactly like DENIED --
    # never render order data on an unconfirmed ownership check, and never
    # let the customer-facing message reveal *why* it failed (see the
    # module's own note on order enumeration).
    class _BrokenService:
        def verify_ownership(self, order_id, customer_id):
            raise ToolError(DATABASE_UNAVAILABLE, "db down", retryable=True)

    monkeypatch.setattr(conversation_router_agent, "get_order_ownership_service", lambda: _BrokenService())

    result = conversation_router_node({
        "user_message": "这个订单 ORD2026080099 什么时候发货",
        "conversation_context": {"authenticated_customer_id": "CUS0001"},
    })

    assert result["conversation_result"]["status"] == "Rejected"
    assert result["customer_response"] == _OWNERSHIP_UNVERIFIED_RESPONSE


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
