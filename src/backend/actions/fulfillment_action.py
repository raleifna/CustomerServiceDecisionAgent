from src.backend.graph.state import CustomerServiceState
from src.backend.repositories.fulfillment_repository import create_expedite_request
from src.backend.repositories.mock_db_repository import get_order


def fulfillment_action_node(
    state: CustomerServiceState,
) -> CustomerServiceState:
    """Create an idempotent expedite-shipment business event."""
    conversation_result = state.get("conversation_result") or {}
    order_id = conversation_result.get("order_id")
    context = dict(state.get("conversation_context") or {})

    if not order_id:
        return {
            "action_result": {
                "status": "Failed",
                "action": "ExpediteShipment",
                "reason": "MissingOrderId",
            },
            "customer_response": "缺少订单号，暂时无法提交催发货请求。",
        }

    order = get_order(order_id)
    if order is None:
        return {
            "action_result": {
                "status": "Failed",
                "action": "ExpediteShipment",
                "order_id": order_id,
                "reason": "OrderNotFound",
            },
            "customer_response": f"没有找到订单 {order_id}，暂时无法提交催单。",
        }

    if order.get("shipped_date"):
        context.pop("pending_action", None)
        return {
            "conversation_context": context,
            "action_result": {
                "status": "NotRequired",
                "action": "ExpediteShipment",
                "order_id": order_id,
                "reason": "AlreadyShipped",
            },
            "customer_response": (
                f"订单 {order_id} 已经发货，无需再提交催发货请求。"
            ),
        }

    customer_id = order.get("customer_id")
    if not customer_id:
        return {
            "action_result": {
                "status": "Failed",
                "action": "ExpediteShipment",
                "order_id": order_id,
                "reason": "MissingCustomerId",
            },
            "customer_response": "订单缺少客户信息，催单暂时无法提交，请联系人工客服。",
        }

    try:
        request, created = create_expedite_request(order_id, customer_id)
    except Exception as exc:
        context["pending_action"] = "HumanExpediteRequired"
        return {
            "conversation_context": context,
            "action_result": {
                "status": "Failed",
                "action": "ExpediteShipment",
                "order_id": order_id,
                "reason": type(exc).__name__,
            },
            "customer_response": (
                "催发货请求暂时未能提交，请联系人工客服继续处理。"
            ),
        }

    request_id = request["fulfillment_request_id"]
    context["active_order_id"] = order_id
    context["pending_action"] = "ExpediteShipmentSubmitted"

    if created:
        response = (
            f"已为订单 {order_id} 提交催发货请求，催单编号为 {request_id}。"
        )
    else:
        response = (
            f"订单 {order_id} 已存在尚未完成的催发货请求，"
            f"催单编号为 {request_id}，无需重复提交。"
        )

    return {
        "conversation_context": context,
        "action_result": {
            "status": "Submitted",
            "action": "ExpediteShipment",
            "order_id": order_id,
            "fulfillment_request_id": request_id,
            "created": created,
        },
        "customer_response": response,
    }
