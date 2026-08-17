from src.backend.agents.request_resolver_agent import (
    ORDER_ID_PATTERN,
    REQUEST_ID_PATTERN,
)
from src.backend.graph.state import CustomerServiceState
from src.backend.llm import chat_json
from src.backend.repositories.mock_db_repository import get_order


VALID_CONVERSATION_INTENTS = {
    "OrderStatusQuery",
    "ShippingTimeQuery",
    "LogisticsQuery",
    "AfterSalesStatusQuery",
    "AfterSalesRequest",
    "ExpediteShipmentConfirmation",
    "Unknown",
}


def _classify_with_keywords(user_message: str) -> str | None:
    after_sales_status_words = (
        "售后进度",
        "申请进度",
        "退款进度",
        "退货进度",
        "换货进度",
        "退款怎么样",
        "换货怎么样",
        "售后怎么样",
    )
    after_sales_action_words = (
        "申请退款",
        "想退款",
        "要退款",
        "申请退货",
        "想退货",
        "申请换货",
        "想换货",
        "取消订单",
        "申请售后",
        "质量问题",
        "商品损坏",
        "少发",
        "错发",
    )
    shipping_time_words = (
        "什么时候发货",
        "多久发货",
        "几号发货",
        "哪天发货",
        "预计发货",
    )
    order_status_words = (
        "发货了吗",
        "发货了没",
        "有没有发货",
        "是否发货",
        "订单状态",
        "签收了吗",
        "是否签收",
    )
    logistics_words = (
        "物流",
        "快递到哪",
        "包裹到哪",
        "什么时候到",
        "预计送达",
        "快递异常",
    )

    if any(word in user_message for word in after_sales_status_words):
        return "AfterSalesStatusQuery"
    if any(word in user_message for word in after_sales_action_words):
        return "AfterSalesRequest"
    if any(word in user_message for word in shipping_time_words):
        return "ShippingTimeQuery"
    if any(word in user_message for word in order_status_words):
        return "OrderStatusQuery"
    if any(word in user_message for word in logistics_words):
        return "LogisticsQuery"

    return None


def _classify_with_llm(user_message: str) -> str:
    result = chat_json(
        (
            "请将下面的用户消息分类为 JSON，唯一字段为 conversation_intent：\n\n"
            f"{user_message}"
        ),
        system_prompt=(
            "你是电商客服消息路由器，只负责分类，不回答问题。"
            "conversation_intent 只能是：OrderStatusQuery（是否发货或签收）、"
            "ShippingTimeQuery（询问什么时候或多久发货）、"
            "LogisticsQuery（快递位置、时效或异常）、AfterSalesStatusQuery"
            "（查询已有退款、退货、换货申请进度）、AfterSalesRequest"
            "（提出退款、退货、换货或商品问题处理请求）、Unknown。"
            "请只输出 JSON。"
        ),
        temperature=0.0,
    )
    intent = result.get("conversation_intent")
    if intent not in VALID_CONVERSATION_INTENTS:
        return "Unknown"
    return intent


def conversation_router_node(
    state: CustomerServiceState,
) -> CustomerServiceState:
    """Classify the current customer turn before entering a business workflow."""
    if state.get("request_id") and not state.get("user_message"):
        return {
            "conversation_result": {
                "status": "Resolved",
                "conversation_intent": "AfterSalesRequest",
                "matched_by": "direct_input",
            }
        }

    user_message = state.get("user_message", "").strip()
    if not user_message:
        return {
            "conversation_result": {
                "status": "NeedsClarification",
                "conversation_intent": "Unknown",
            },
            "customer_response": "请告诉我您想查询订单、物流，还是处理售后问题。",
        }

    context = dict(state.get("conversation_context") or {})
    order_match = ORDER_ID_PATTERN.search(user_message)
    request_match = REQUEST_ID_PATTERN.search(user_message)
    explicit_order_id = order_match.group().upper() if order_match else None
    explicit_request_id = request_match.group().upper() if request_match else None

    confirmation_words = {
        "需要",
        "好的",
        "好",
        "可以",
        "帮我催一下",
        "催一下",
        "麻烦催一下",
    }
    normalized_message = user_message.strip(" ，。！？!?、")
    if (
        normalized_message in confirmation_words
        and context.get("pending_action") == "OfferExpediteShipment"
    ):
        order_id = context.get("active_order_id")
        context["pending_action"] = "ExpediteShipmentConfirmed"
        context["last_intent"] = "ExpediteShipmentConfirmation"
        return {
            "conversation_context": context,
            "conversation_result": {
                "status": "Resolved",
                "conversation_intent": "ExpediteShipmentConfirmation",
                "matched_by": "conversation_context",
                "order_id": order_id,
                "request_id": context.get("active_request_id"),
            },
        }

    if context.get("pending_action") == "CreateServiceRequest":
        intent = "AfterSalesRequest"
        matched_by = "conversation_context"
    else:
        intent = _classify_with_keywords(user_message)
        matched_by = "keywords"

    if intent is None:
        try:
            intent = _classify_with_llm(user_message)
            matched_by = "llm"
        except Exception:
            intent = "Unknown"
            matched_by = "fallback"

    order_id = explicit_order_id or context.get("active_order_id")
    request_id = explicit_request_id or context.get("active_request_id")

    authenticated_customer_id = context.get("authenticated_customer_id")
    if order_id and authenticated_customer_id:
        order = get_order(order_id)
        if order is None or order.get("customer_id") != authenticated_customer_id:
            context.pop("active_order_id", None)
            context.pop("active_request_id", None)
            context.pop("pending_action", None)
            context["last_intent"] = "Unknown"
            return {
                "conversation_context": context,
                "conversation_result": {
                    "status": "Rejected",
                    "conversation_intent": "Unknown",
                    "matched_by": "authorization_guard",
                    "order_id": None,
                    "request_id": None,
                },
                "customer_response": "该订单不属于当前账户，请检查订单号后重试。",
            }

    if not explicit_order_id and order_id and matched_by != "llm":
        matched_by = "conversation_context"

    if explicit_order_id and explicit_order_id != context.get("active_order_id"):
        context.pop("active_request_id", None)
        context.pop("pending_action", None)
    if order_id:
        context["active_order_id"] = order_id
    if request_id:
        context["active_request_id"] = request_id
    context["last_intent"] = intent

    result = {
        "status": "Resolved" if intent != "Unknown" else "NeedsClarification",
        "conversation_intent": intent,
        "matched_by": matched_by,
        "order_id": order_id,
        "request_id": request_id,
    }

    update: CustomerServiceState = {
        "conversation_context": context,
        "conversation_result": result,
    }
    if intent == "Unknown":
        update["customer_response"] = (
            "我还不能确定您的需求。请说明您要查询订单、物流、售后进度，"
            "还是申请退款或换货。"
        )
    return update
