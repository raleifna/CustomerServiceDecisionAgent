from src.backend.graph.state import CustomerServiceState
from src.backend.repositories.fulfillment_repository import create_logistics_chase_request
from src.backend.repositories.mock_db_repository import get_logistics, get_order


def logistics_chase_action_node(
    state: CustomerServiceState,
) -> CustomerServiceState:
    """Create an idempotent 'chase up stalled in-transit logistics' event.

    Distinct from fulfillment_action_node's ExpediteShipment: that one is for
    orders that haven't shipped yet; this is for orders that HAVE shipped but
    whose tracking has gone quiet — a different point in the order lifecycle,
    so it needs its own guard (must have shipped) and its own request type.
    """
    conversation_result = state.get("conversation_result") or {}
    order_id = conversation_result.get("order_id")
    context = dict(state.get("conversation_context") or {})

    if not order_id:
        return {
            "action_result": {
                "status": "Failed",
                "action": "LogisticsChase",
                "reason": "MissingOrderId",
            },
            "customer_response": "缺少订单号，暂时无法提交催物流请求。",
        }

    order = get_order(order_id)
    if order is None:
        return {
            "action_result": {
                "status": "Failed",
                "action": "LogisticsChase",
                "order_id": order_id,
                "reason": "OrderNotFound",
            },
            "customer_response": f"没有找到订单 {order_id}，暂时无法提交催物流请求。",
        }

    if not order.get("shipped_date"):
        context.pop("pending_action", None)
        return {
            "conversation_context": context,
            "action_result": {
                "status": "NotRequired",
                "action": "LogisticsChase",
                "order_id": order_id,
                "reason": "NotYetShipped",
            },
            "customer_response": (
                f"订单 {order_id} 还没有发货，暂时没有物流记录可以催，"
                "如需要可以帮您催发货。"
            ),
        }

    if order.get("delivered_date"):
        context.pop("pending_action", None)
        return {
            "conversation_context": context,
            "action_result": {
                "status": "NotRequired",
                "action": "LogisticsChase",
                "order_id": order_id,
                "reason": "AlreadyDelivered",
            },
            "customer_response": f"订单 {order_id} 已经签收，无需再催物流。",
        }

    customer_id = order.get("customer_id")
    if not customer_id:
        return {
            "action_result": {
                "status": "Failed",
                "action": "LogisticsChase",
                "order_id": order_id,
                "reason": "MissingCustomerId",
            },
            "customer_response": "订单缺少客户信息，催物流暂时无法提交，请联系人工客服。",
        }

    try:
        request, created = create_logistics_chase_request(order_id, customer_id)
    except Exception as exc:
        context["pending_action"] = "HumanLogisticsChaseRequired"
        return {
            "conversation_context": context,
            "action_result": {
                "status": "Failed",
                "action": "LogisticsChase",
                "order_id": order_id,
                "reason": type(exc).__name__,
            },
            "customer_response": "催物流请求暂时未能提交，请联系人工客服继续处理。",
        }

    request_id = request["fulfillment_request_id"]
    context["active_order_id"] = order_id
    context["pending_action"] = "LogisticsChaseSubmitted"

    exceptions = get_logistics(order_id)
    exception_note = ""
    if any(not item.get("resolved") for item in exceptions):
        exception_note = "系统已记录该订单存在未处理的物流异常，会一并核实。"

    if created:
        response = (
            f"已为订单 {order_id} 提交催物流请求，工单编号为 {request_id}，"
            f"我们会联系承运商核实包裹当前情况。{exception_note}"
        )
    else:
        response = (
            f"订单 {order_id} 已存在尚未完成的催物流请求，"
            f"工单编号为 {request_id}，无需重复提交。"
        )

    return {
        "conversation_context": context,
        "action_result": {
            "status": "Submitted",
            "action": "LogisticsChase",
            "order_id": order_id,
            "fulfillment_request_id": request_id,
            "created": created,
        },
        "customer_response": response,
    }
