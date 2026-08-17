import os
from typing import Any

from src.backend.graph.state import CustomerServiceState
from src.backend.llm import chat
from src.backend.repositories.mock_db_repository import (
    get_logistics,
    get_order,
    get_orders_by_customer_id,
)


def _query_order_id(state: CustomerServiceState) -> str | None:
    conversation_result = state.get("conversation_result", {})
    order_id = conversation_result.get("order_id")
    return order_id if isinstance(order_id, str) else None


def _resolve_order_for_customer(
    state: CustomerServiceState,
) -> tuple[str | None, str | None]:
    """Resolve a sole customer order or ask them to choose among candidates."""
    order_id = _query_order_id(state)
    if order_id:
        return order_id, None

    context = state.get("conversation_context") or {}
    customer_id = context.get("authenticated_customer_id")
    if not isinstance(customer_id, str):
        return None, "请提供订单号，我才能继续查询。"

    orders = get_orders_by_customer_id(customer_id)
    if len(orders) == 1:
        return orders[0].get("order_id"), None
    if not orders:
        return None, "当前账户下没有可以查询的订单。"

    status_labels = []
    for order in orders:
        if order.get("delivered_date"):
            label = "已签收"
        elif order.get("shipped_date"):
            label = "运输中"
        else:
            label = "待发货"
        status_labels.append(f"{order.get('order_id')}（{label}）")

    return None, (
        "您有多笔订单，请选择要查询的订单："
        + "、".join(status_labels)
        + "。"
    )


def _polish_query_response(
    template: str,
    protected_facts: list[str],
) -> str:
    enabled = os.getenv("ENABLE_LLM_RESPONSE", "false").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        return template

    try:
        polished = chat(
            f"请润色下面的客服查询回复，保留全部事实：\n\n{template}",
            system_prompt=(
                "你是电商客服回复编辑器，只能润色已有查询结果。"
                "不得改变订单状态、日期、承运商、物流异常或售后状态，"
                "不得补充输入中没有的物流轨迹，也不得承诺退款或赔偿。"
                "请保持简洁自然，只输出回复正文。"
            ),
            temperature=0.2,
        )
    except Exception:
        return template

    if not polished or len(polished) > max(len(template) * 3, 200):
        return template
    if any(fact not in polished for fact in protected_facts if fact):
        return template

    return polished


def order_status_query_node(
    state: CustomerServiceState,
) -> CustomerServiceState:
    """Answer shipping and delivery status questions from order facts."""
    order_id, clarification = _resolve_order_for_customer(state)
    if not order_id:
        return {"customer_response": clarification or "请提供订单号，我才能查询是否发货。"}

    order = get_order(order_id)
    if order is None:
        return {"customer_response": f"没有找到订单 {order_id}，请核对订单号。"}

    shipped_date = order.get("shipped_date")
    promised_ship_date = order.get("promised_ship_date")
    delivered_date = order.get("delivered_date")
    estimated_date = order.get("estimated_delivery_date")
    conversation_intent = state.get("conversation_result", {}).get(
        "conversation_intent"
    )

    if conversation_intent == "ShippingTimeQuery" and shipped_date:
        response = f"订单 {order_id} 已于 {shipped_date} 发货。"
        protected_facts = [order_id, shipped_date, "已于", "发货"]
    elif conversation_intent == "ShippingTimeQuery":
        if promised_ship_date:
            response = (
                f"订单 {order_id} 已完成支付，目前尚未发货。"
                f"商家承诺在 {promised_ship_date} 前发出。"
                "如需催发货，可联系人工客服协助处理。"
            )
            protected_facts = [
                order_id,
                "已完成支付",
                "未发货",
                promised_ship_date,
                "承诺",
                "人工客服",
            ]
        else:
            response = (
                f"订单 {order_id} 已完成支付，但目前尚未发货。"
                "订单记录中没有可确认的预计发货日期，因此暂时无法准确告知发货时间"
            )
            protected_facts = [
                order_id,
                "已完成支付",
                "未发货",
                "无法准确告知发货时间",
            ]
        if not promised_ship_date and estimated_date:
            response += (
                f"；当前记录的预计送达日期为 {estimated_date}，"
                "但它不等同于承诺发货日期"
            )
            protected_facts.extend([estimated_date, "不等同于承诺发货日期"])
        if not response.endswith("。"):
            response += "。"
    elif delivered_date:
        response = f"订单 {order_id} 已发货，并已于 {delivered_date} 签收。"
        protected_facts = [order_id, delivered_date, "已发货", "签收"]
    elif shipped_date:
        response = f"订单 {order_id} 已于 {shipped_date} 发货"
        protected_facts = [order_id, shipped_date, "发货"]
        if estimated_date:
            response += f"，预计 {estimated_date} 送达"
            protected_facts.append(estimated_date)
        response += "。"
    else:
        response = f"订单 {order_id} 目前尚未发货"
        protected_facts = [order_id, "未发货"]
        if order.get("payment_status") == "Paid":
            response += "，订单已完成支付"
            protected_facts.append("已完成支付")
        response += "。"

    response = _polish_query_response(response, protected_facts)
    conversation_context = dict(state.get("conversation_context") or {})
    conversation_context["active_order_id"] = order_id
    if (
        conversation_intent == "ShippingTimeQuery"
        and not shipped_date
        and promised_ship_date
    ):
        conversation_context["pending_action"] = "OfferExpediteShipment"

    return {
        "conversation_context": conversation_context,
        "order": order,
        "query_result": {
            "query_type": conversation_intent or "OrderStatusQuery",
            "order_id": order_id,
            "is_shipped": bool(shipped_date),
            "is_delivered": bool(delivered_date),
            "promised_ship_date": promised_ship_date,
        },
        "customer_response": response,
    }


def _format_exception(exception: dict[str, Any]) -> str:
    exception_type = exception.get("exception_type", "未知异常")
    resolution = "已处理" if exception.get("resolved") else "尚未处理完成"
    return f"{exception_type}（{resolution}）"


def logistics_query_node(
    state: CustomerServiceState,
) -> CustomerServiceState:
    """Answer logistics questions without inventing unavailable tracking events."""
    order_id, clarification = _resolve_order_for_customer(state)
    if not order_id:
        return {"customer_response": clarification or "请提供订单号，我才能查询物流信息。"}

    order = get_order(order_id)
    if order is None:
        return {"customer_response": f"没有找到订单 {order_id}，请核对订单号。"}

    logistics = get_logistics(order_id)
    shipped_date = order.get("shipped_date")
    delivered_date = order.get("delivered_date")
    estimated_date = order.get("estimated_delivery_date")

    if not shipped_date:
        response = f"订单 {order_id} 尚未发货，目前还没有可查询的物流信息。"
        protected_facts = [order_id, "未发货", "没有可查询的物流信息"]
    elif delivered_date:
        response = f"订单 {order_id} 已于 {delivered_date} 签收。"
        protected_facts = [order_id, delivered_date, "签收"]
    else:
        response = f"订单 {order_id} 已于 {shipped_date} 发货"
        protected_facts = [order_id, shipped_date, "发货"]
        if estimated_date:
            response += f"，预计 {estimated_date} 送达"
            protected_facts.append(estimated_date)
        response += "。"

    if logistics:
        carriers = sorted(
            {
                item["carrier"]
                for item in logistics
                if isinstance(item.get("carrier"), str)
            }
        )
        if carriers:
            response += f" 承运商：{'、'.join(carriers)}。"
            protected_facts.extend(carriers)

        exceptions = [_format_exception(item) for item in logistics]
        response += f" 当前物流记录包含：{'；'.join(exceptions)}。"
        for item in logistics:
            exception_type = item.get("exception_type")
            if isinstance(exception_type, str):
                protected_facts.append(exception_type)
            protected_facts.append("已处理" if item.get("resolved") else "尚未处理")

    response = _polish_query_response(response, protected_facts)

    conversation_context = dict(state.get("conversation_context") or {})
    conversation_context["active_order_id"] = order_id

    return {
        "conversation_context": conversation_context,
        "order": order,
        "logistics": logistics,
        "query_result": {
            "query_type": "LogisticsQuery",
            "order_id": order_id,
            "exception_count": len(logistics),
        },
        "customer_response": response,
    }


def service_request_status_query_node(
    state: CustomerServiceState,
) -> CustomerServiceState:
    """Report the stored workflow status without re-running the decision engine."""
    service_request = state.get("service_request")
    if service_request is None:
        return {"customer_response": "暂时没有找到对应的售后申请。"}

    request_id = service_request.get("request_id")
    workflow_status = service_request.get("workflow_status", "Pending")
    decision = service_request.get("decision")

    status_labels = {
        "Pending": "待处理",
        "InReview": "审核中",
        "NeedsReflection": "需要进一步复核",
        "EscalatedToHuman": "已转人工审核",
        "Closed": "已关闭",
    }
    decision_labels = {
        "Approve": "已批准",
        "PartialApprove": "已部分批准",
        "Reject": "未通过",
    }

    response = (
        f"售后申请 {request_id} 当前状态为："
        f"{status_labels.get(workflow_status, workflow_status)}。"
    )
    protected_facts = [
        str(request_id),
        status_labels.get(workflow_status, workflow_status),
    ]
    if decision:
        decision_label = decision_labels.get(decision, decision)
        response += f"审核结论：{decision_label}。"
        protected_facts.append(decision_label)
    else:
        response += "目前还没有最终审核结论。"
        protected_facts.append("没有最终审核结论")

    response = _polish_query_response(response, protected_facts)

    conversation_context = dict(state.get("conversation_context") or {})
    conversation_context["active_order_id"] = service_request.get("order_id")
    conversation_context["active_request_id"] = request_id

    return {
        "conversation_context": conversation_context,
        "query_result": {
            "query_type": "AfterSalesStatusQuery",
            "request_id": request_id,
            "workflow_status": workflow_status,
            "decision": decision,
        },
        "customer_response": response,
    }
