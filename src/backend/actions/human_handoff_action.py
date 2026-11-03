from src.backend.graph.state import CustomerServiceState
from src.backend.repositories.live_chat_repository import LiveChatUnavailable, create_or_reuse_handoff


def human_handoff_action_node(state: CustomerServiceState) -> CustomerServiceState:
    context = dict(state.get("conversation_context") or {})
    # A handoff confirmed from a pending offer arrives with the customer's
    # bare "需要"/"是的" as user_message — the real reason was recorded when
    # the offer was made, not in this turn's message.
    reason = (
        context.pop("pending_human_handoff_reason", None)
        or state.get("user_message")
        or "客户要求转人工"
    )
    customer_id = context.get("authenticated_customer_id")
    try:
        handoff = create_or_reuse_handoff(
            customer_id=customer_id,
            reason=reason,
            order_id=context.get("active_order_id"),
            request_id=context.get("active_request_id"),
            # The graph thread id, so staff can read this AI conversation.
            conversation_id=state.get("conversation_id"),
        )
    except LiveChatUnavailable:
        handoff = None

    if handoff is None:
        # Local fixture mode has no database to queue the handoff in.
        return {
            "conversation_context": context,
            "action_result": {"action": "CreateHumanHandoff", "status": "Unavailable", "handoff_id": None},
            "customer_response": "当前环境暂时无法接入人工客服，请稍后再试，或继续描述问题由智能助手为您处理。",
        }

    context["active_handoff_id"] = handoff["handoff_id"]
    if handoff["workflow_status"] == "Active":
        message = f"人工客服 {handoff['staff_id']} 正在为您服务，请直接在这里继续沟通。"
    else:
        message = "已为您转接人工客服，正在排队。人工客服会查看当前订单和本次对话，您无需重复描述，可以先补充想说的话。"
    return {
        "conversation_context": context,
        "action_result": {"action": "CreateHumanHandoff", "status": "Created", "handoff_id": handoff["handoff_id"]},
        "customer_response": message,
    }
