import re

from src.backend.graph.state import CustomerServiceState
from src.backend.repositories.mock_db_repository import (
    get_order,
    get_orders_by_customer_id,
)
from src.backend.repositories.service_request_repository import create_service_request


def _extract_details(message: str) -> dict:
    if "换货" in message:
        request_type = "Exchange"
    elif "部分退款" in message or "部分退" in message:
        request_type = "PartialRefund"
    elif any(word in message for word in ("退款", "退货", "取消订单", "不想要")):
        request_type = "Refund"
    else:
        request_type = None

    reason_patterns = (
        (("少发", "漏发", "少了"), "少发商品"),
        (("错发", "发错"), "错发商品"),
        (("损坏", "破损", "坏了", "坏的"), "商品损坏"),
        (("质量问题", "有问题", "故障"), "质量问题"),
        (("没收到", "未收到", "丢件"), "未收到货"),
        (("物流延误", "物流太慢", "一直没到", "延迟"), "物流延误"),
        (("取消订单", "还没发货"), "未发货取消订单"),
        (("不想要", "不喜欢", "七天无理由"), "不想要了"),
    )
    request_reason = None
    for words, normalized in reason_patterns:
        if any(word in message for word in words):
            request_reason = normalized
            break

    quantity_match = re.search(r"(\d+)\s*件", message)
    quantity = int(quantity_match.group(1)) if quantity_match else None
    return {
        "request_type": request_type,
        "request_reason": request_reason,
        "requested_quantity": quantity,
    }


def _candidate_orders(customer_id: str) -> str:
    orders = get_orders_by_customer_id(customer_id)
    candidates = []
    for order in orders:
        if order.get("delivered_date"):
            status = "已签收"
        elif order.get("shipped_date"):
            status = "运输中"
        else:
            status = "待发货"
        candidates.append(f"{order['order_id']}（{status}）")
    return "、".join(candidates)


def service_request_action_node(
    state: CustomerServiceState,
) -> CustomerServiceState:
    """Validate and persist a newly stated after-sales request."""
    context = dict(state.get("conversation_context") or {})
    conversation_result = state.get("conversation_result") or {}
    customer_id = context.get("authenticated_customer_id")
    order_id = conversation_result.get("order_id") or context.get("active_order_id")

    if not customer_id:
        return {"customer_response": "缺少客户身份，暂时无法创建售后申请。"}

    pending = dict(context.get("pending_service_request") or {})
    extracted = _extract_details(state.get("user_message", ""))
    for field, value in extracted.items():
        if value is not None:
            pending[field] = value
    context["pending_service_request"] = pending

    if not order_id:
        orders = get_orders_by_customer_id(customer_id)
        if len(orders) == 1:
            order_id = orders[0]["order_id"]
        elif len(orders) > 1:
            context["pending_action"] = "CreateServiceRequest"
            return {
                "conversation_context": context,
                "customer_response": (
                    "您有多笔订单，请先选择需要售后的订单："
                    f"{_candidate_orders(customer_id)}。"
                ),
            }
        else:
            return {"customer_response": "当前账户下没有可以申请售后的订单。"}

    order = get_order(order_id)
    if order is None or order.get("customer_id") != customer_id:
        return {"customer_response": "该订单不属于当前账户，无法创建售后申请。"}

    pending["order_id"] = order_id
    context["pending_service_request"] = pending
    context["active_order_id"] = order_id

    if not pending.get("request_type"):
        context["pending_action"] = "CreateServiceRequest"
        return {
            "conversation_context": context,
            "customer_response": "请问您希望申请退款、部分退款，还是换货？",
        }
    if not pending.get("request_reason"):
        context["pending_action"] = "CreateServiceRequest"
        return {
            "conversation_context": context,
            "customer_response": "请简单说明申请原因，例如商品损坏、质量问题、少发或未收到货。",
        }

    requested_quantity = pending.get("requested_quantity")
    ordered_quantity = order.get("quantity")
    if requested_quantity is None and ordered_quantity == 1:
        requested_quantity = 1
    if requested_quantity is None:
        context["pending_action"] = "CreateServiceRequest"
        return {
            "conversation_context": context,
            "customer_response": f"该订单包含 {ordered_quantity} 件商品，请说明需要售后的数量。",
        }
    if not isinstance(ordered_quantity, int) or requested_quantity > ordered_quantity:
        return {
            "conversation_context": context,
            "customer_response": f"申请数量不能超过订单中的 {ordered_quantity} 件，请重新确认。",
        }

    try:
        service_request, created = create_service_request(
            order_id=order_id,
            customer_id=customer_id,
            request_type=pending["request_type"],
            request_reason=pending["request_reason"],
            requested_quantity=requested_quantity,
        )
    except Exception as exc:
        return {
            "action_result": {
                "status": "Failed",
                "action": "CreateServiceRequest",
                "reason": type(exc).__name__,
            },
            "customer_response": "售后申请暂时未能创建，请稍后重试或联系人工客服。",
        }

    request_id = service_request["request_id"]
    context.pop("pending_service_request", None)
    context["pending_action"] = "AwaitEvidence"
    context["active_request_id"] = request_id

    if created:
        message = (
            f"已为订单 {order_id} 创建售后申请 {request_id}，当前状态为待处理。"
            "如果是商品损坏或质量问题，请继续提交清晰的图片或视频证据；"
            "申请不会仅凭文字描述自动批准。"
        )
    else:
        message = f"该订单已有相同的未关闭售后申请 {request_id}，无需重复创建。"

    return {
        "conversation_context": context,
        "request_id": request_id,
        "service_request": service_request,
        "action_result": {
            "status": "Created" if created else "Existing",
            "action": "CreateServiceRequest",
            "request_id": request_id,
            "created": created,
        },
        "customer_response": message,
    }
