import os
import re
from datetime import date
from typing import Any

from src.backend.graph.state import CustomerServiceState
from src.backend.llm import chat, chat_with_tools
from src.backend.repositories.mock_db_repository import (
    get_logistics,
    get_logistics_tracking,
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


QUERY_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_order_status",
            "description": "查询当前订单的支付状态、发货状态、签收状态，以及承诺/预计的发货或送达时间。",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_logistics_status",
            "description": (
                "查询当前订单的物流信息：承运商、运单号、当前所在城市/网点、"
                "完整的物流跟踪节点历史（快件到哪了），以及是否存在物流长期未更新、"
                "长期未更新、路径偏离或派送异常记录。商品或包装破损不属于"
                "物流轨迹系统可确认的事实，需要客户另行发起售后并提交材料。"
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]

def _build_query_system_prompt(
    *,
    can_offer_expedite: bool = False,
    is_overdue: bool = False,
    can_offer_logistics_chase: bool = False,
) -> str:
    prompt = (
        "你是电商平台的客服。你只能依据工具返回的事实回答，不得编造未查询到的"
        "物流节点、日期、承运商或状态；工具结果里没有的信息就直接说查不到。"
        "涉及日期时必须和今天比较：已经过了的承诺/预计日期要明确说\"已逾期\"，"
        "没过的就正常陈述日期，不要额外加\"请放心\"\"这是正常的\"之类的安抚语，"
        "也不要提醒用户当前日期是几号。"
        "回复必须包含：订单号、当前状态（已发货/未发货/已签收/逾期）、"
        "相关日期（发货日期、预计送达日期，按订单实际情况给），但不要写成"
        "\"订单号...当前状态...发货日期...\"这种逐项罗列、字段堆砌的报告体，"
        "改成一到两句连贯自然的话说出来，像人在说话，不要额外加解释性、"
        "安抚性或铺垫性的句子。"
        "回复是纯文本聊天气泡，不支持 Markdown，不要使用星号加粗、标题或列表符号。"
        "客户问快递到哪了/物流进度时，用 tracking_checkpoints 里时间最新的"
        "那条作为包裹当前所在位置。"
    )
    if can_offer_expedite:
        if is_overdue:
            prompt += (
                "这个订单已经超过承诺发货日期，属于逾期未发货。"
                "在回答完事实之后，必须主动、明确地提出可以立即帮客户提交催发货请求，"
                "并询问客户是否需要现在处理，不要只是建议客户自己去联系商家。"
            )
        else:
            prompt += (
                "这个订单还没发货但有承诺发货日期，尚未逾期。"
                "在回答完事实之后，可以主动询问客户是否需要现在就帮忙提交催发货请求。"
            )
    if can_offer_logistics_chase:
        prompt += (
            "这个订单已经发货，但预计送达时间已过或物流跟踪长时间没有更新。"
            "按这个顺序说：先说清楚物流确实延迟了，再建议客户耐心等待，"
            "最后主动提出如果物流一直没有更新，可以由客服帮忙催物流并询问是否需要现在处理。"
        )
    return prompt


def _build_tool_executor(order: dict[str, Any], logistics: list[dict[str, Any]]):
    order_id = order.get("order_id")

    def execute(name: str, _arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "get_order_status":
            return {
                "today": date.today().isoformat(),
                "order_id": order_id,
                "payment_status": order.get("payment_status"),
                "shipped_date": order.get("shipped_date"),
                "promised_ship_date": order.get("promised_ship_date"),
                "delivered_date": order.get("delivered_date"),
                "estimated_delivery_date": order.get("estimated_delivery_date"),
            }
        if name == "get_logistics_status":
            tracking = get_logistics_tracking(order_id)
            return {
                "order_id": order_id,
                "carrier": tracking.get("carrier") if tracking else None,
                "tracking_number": tracking.get("tracking_number") if tracking else None,
                "current_status": tracking.get("current_status") if tracking else None,
                # Chronological order, earliest first — the checkpoint with
                # the latest time is where the package currently is.
                "tracking_checkpoints": tracking.get("checkpoints", [])
                if tracking
                else [],
                "exceptions": [
                    {
                        "carrier": item.get("carrier"),
                        "exception_type": item.get("exception_type"),
                        "exception_recorded_date": item.get(
                            "exception_recorded_date"
                        ),
                        "resolved": item.get("resolved"),
                    }
                    for item in logistics
                ],
            }
        return {"error": f"未知工具: {name}"}

    return execute


def _fallback_query_response(
    order: dict[str, Any],
    logistics: list[dict[str, Any]],
) -> str:
    """Deterministic degrade for when the tool-calling round trip fails."""
    order_id = order.get("order_id")
    parts = [f"订单 {order_id}"]

    delivered_date = order.get("delivered_date")
    shipped_date = order.get("shipped_date")
    promised_ship_date = order.get("promised_ship_date")

    if delivered_date:
        parts.append(f"已于 {delivered_date} 签收")
    elif shipped_date:
        parts.append(f"已于 {shipped_date} 发货")
    elif promised_ship_date:
        parts.append(f"尚未发货，承诺发货日期为 {promised_ship_date}")
    else:
        parts.append("尚未发货")

    if logistics:
        exception_texts = []
        for item in logistics:
            status = "已处理" if item.get("resolved") else "尚未处理"
            exception_texts.append(f"{item.get('carrier')} {item.get('exception_type')}（{status}）")
        parts.append("物流记录：" + "；".join(exception_texts))
    else:
        tracking = get_logistics_tracking(order_id)
        checkpoints = tracking.get("checkpoints") if tracking else None
        if checkpoints:
            latest = checkpoints[-1]
            parts.append(f"包裹目前位置：{latest.get('location')}（{latest.get('time')}）")

    return "，".join(parts) + "。当前查询助手暂时繁忙，以上为基础信息，如需更多细节请稍后重试。"


def _system_managed_stall_response(
    order: dict[str, Any],
    logistics: list[dict[str, Any]],
) -> str | None:
    """Explain a monitor-detected tracking stall without offering it again.

    A stale carrier feed is observable by the platform, so the demo records it
    as an anomaly that is already being processed.  It is not evidence that the
    parcel is lost; that claim still has to come from the customer or carrier.
    """
    unresolved_stalls = [
        item
        for item in logistics
        if not item.get("resolved")
        and any(
            keyword in str(item.get("exception_type") or "")
            for keyword in ("停滞", "滞留", "长期未更新")
        )
    ]
    if not unresolved_stalls:
        return None

    order_id = order.get("order_id")
    tracking = get_logistics_tracking(order_id)
    carrier = (
        (tracking or {}).get("carrier")
        or unresolved_stalls[0].get("carrier")
        or "承运商"
    )
    tracking_number = (tracking or {}).get("tracking_number")
    checkpoints = (tracking or {}).get("checkpoints") or []

    tracking_label = f"，运单号 {tracking_number}" if tracking_number else ""
    latest_text = ""
    if checkpoints:
        latest = checkpoints[-1]
        latest_time = latest.get("time")
        latest_location = latest.get("location")
        if latest_time and latest_location:
            latest_text = f"最新一条轨迹是 {latest_time} 到达{latest_location}。"
        elif latest_location:
            latest_text = f"最新一条轨迹显示包裹位于{latest_location}。"

    estimated_date = order.get("estimated_delivery_date")
    overdue_text = (
        f"原预计 {estimated_date} 送达，目前已经超出预计时间。"
        if estimated_date and estimated_date < date.today().isoformat()
        else ""
    )

    return (
        f"订单 {order_id} 由{carrier}承运{tracking_label}。"
        f"{latest_text}{overdue_text}物流已停滞超过72小时，需不需要帮您催一下？"
    )


def _strip_markdown_artifacts(text: str) -> str:
    """Deterministic backstop: the system prompt asks the model not to use
    Markdown (chat bubbles render plain text), but instruction-following
    isn't guaranteed, so strip the common cases rather than trust it blindly.
    """
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^[-*]\s+", "", text, flags=re.MULTILINE)
    return text


def query_agent_node(state: CustomerServiceState) -> CustomerServiceState:
    """Answer order-status/shipping-time/logistics questions via LLM tool calling.

    Order resolution stays deterministic (same as before) so the LLM is only
    ever handed tools scoped to an order the caller is already authorized to
    see — it never chooses which order to query, only which facts to fetch.
    """
    order_id, clarification = _resolve_order_for_customer(state)
    if not order_id:
        return {"customer_response": clarification or "请提供订单号，我才能查询。"}

    order = get_order(order_id)
    if order is None:
        return {"customer_response": f"没有找到订单 {order_id}，请核对订单号。"}

    logistics = get_logistics(order_id)
    conversation_intent = state.get("conversation_result", {}).get(
        "conversation_intent"
    )
    user_message = state.get("user_message", "")
    history = (state.get("dialogue_history") or [])[-8:]

    shipped_date = order.get("shipped_date")
    delivered_date = order.get("delivered_date")
    promised_ship_date = order.get("promised_ship_date")
    estimated_delivery_date = order.get("estimated_delivery_date")
    today_iso = date.today().isoformat()

    # Whether to offer an expedite request is a fact about order state (unshipped,
    # has a promised date), not about which specific question the customer asked —
    # someone asking about logistics on an unshipped order should get the same offer
    # as someone asking "when will it ship".
    can_offer_expedite = bool(not shipped_date and promised_ship_date)
    is_overdue = bool(
        can_offer_expedite and promised_ship_date < today_iso
    )

    # A monitor-detected tracking stall is already in the platform workflow;
    # do not ask the customer whether to create the same chase again.
    managed_stall_response = _system_managed_stall_response(order, logistics)

    # Other shipped-but-not-delivered delays may still be offered a chase.
    can_offer_logistics_chase = False
    if shipped_date and not delivered_date and managed_stall_response is None:
        if estimated_delivery_date and estimated_delivery_date < today_iso:
            can_offer_logistics_chase = True
        elif any(not item.get("resolved") for item in logistics):
            can_offer_logistics_chase = True
        else:
            tracking = get_logistics_tracking(order_id)
            checkpoints = tracking.get("checkpoints") if tracking else None
            if checkpoints:
                latest_date_text = checkpoints[-1].get("time", "").split(" ")[0]
                if latest_date_text and latest_date_text < today_iso:
                    days_stale = (
                        date.today() - date.fromisoformat(latest_date_text)
                    ).days
                    can_offer_logistics_chase = days_stale >= 2

    if conversation_intent == "LogisticsQuery" and managed_stall_response:
        response = managed_stall_response
    else:
        try:
            response = chat_with_tools(
                [*history, {"role": "user", "content": user_message}],
                tools=QUERY_TOOLS,
                tool_executor=_build_tool_executor(order, logistics),
                system_prompt=_build_query_system_prompt(
                    can_offer_expedite=can_offer_expedite,
                    is_overdue=is_overdue,
                    can_offer_logistics_chase=can_offer_logistics_chase,
                ),
            )
        except Exception:
            response = _fallback_query_response(order, logistics)
        else:
            response = _strip_markdown_artifacts(response)

    conversation_context = dict(state.get("conversation_context") or {})
    conversation_context["active_order_id"] = order_id

    if can_offer_expedite:
        conversation_context["pending_action"] = "OfferExpediteShipment"
    elif can_offer_logistics_chase:
        conversation_context["pending_action"] = "OfferLogisticsChase"

    return {
        "conversation_context": conversation_context,
        "order": order,
        "logistics": logistics,
        "query_result": {
            "query_type": conversation_intent or "OrderQuery",
            "order_id": order_id,
            "is_shipped": bool(shipped_date),
            "is_delivered": bool(order.get("delivered_date")),
            "promised_ship_date": promised_ship_date,
            "exception_count": len(logistics),
        },
        "customer_response": response,
    }


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
                "原文经常是逐项罗列的格式（订单号...当前状态...发货日期...），"
                "请把它改写成一两句连贯自然的话，像人在说话，而不是照读字段列表；"
                "同时保持专业、简洁，不要堆砌语气词。只输出回复正文。"
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
