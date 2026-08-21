from src.backend.graph.state import CustomerServiceState
from src.backend.repositories.service_request_repository import withdraw_service_request


def withdraw_service_request_action_node(state: CustomerServiceState) -> CustomerServiceState:
    context = dict(state.get("conversation_context") or {})
    result = state.get("conversation_result") or {}
    request_id = result.get("request_id") or context.get("active_request_id")
    customer_id = context.get("authenticated_customer_id")
    if not request_id:
        return {"customer_response": "当前会话中没有可撤销的售后申请，请先选择具体申请。"}
    if not customer_id:
        return {"customer_response": "当前客户身份无法确认，暂时不能撤销申请。"}
    withdrawn = withdraw_service_request(request_id, customer_id)
    if withdrawn is None:
        return {"customer_response": "该申请已完成、已进入退款或履约阶段，当前不能撤销。您可以查看申请进度确认最新状态。"}
    context.pop("active_request_id", None)
    context.pop("pending_action", None)
    context.pop("pending_service_request", None)
    context.pop("pending_order_confirmation", None)
    return {
        "conversation_context": context,
        "action_result": {
            "status": "Withdrawn",
            "action": "WithdrawServiceRequest",
            # The closed request remains in history, but must not stay bound
            # as the active request or the browser will keep polling it while
            # the customer starts a new application.
            "withdrawn_request_id": request_id,
        },
        "customer_response": f"售后申请 {request_id} 已按您的要求撤销并关闭，不会继续审核或执行退款。",
    }
