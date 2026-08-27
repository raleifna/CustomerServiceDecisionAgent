import re

from src.backend.agents.request_resolver_agent import (
    ORDER_ID_PATTERN,
    REQUEST_ID_PATTERN,
)
from src.backend.graph.state import CustomerServiceState
from src.backend.llm import chat, chat_json
from src.backend.repositories.mock_db_repository import get_order


VALID_CONVERSATION_INTENTS = {
    "OrderStatusQuery",
    "ShippingTimeQuery",
    "LogisticsQuery",
    "AfterSalesStatusQuery",
    "AfterSalesRequest",
    "WithdrawServiceRequest",
    "ExpediteShipmentConfirmation",
    "LogisticsChaseConfirmation",
    "HumanHandoff",
    "Greeting",
    "Unknown",
}

_GREETING_PATTERN = re.compile(
    r"嗨|你好|您好|哈喽|在吗|在么|有人吗|早上好|中午好|下午好|晚上好"
    r"|\bhi\b|\bhello\b|\bhey\b",
    re.IGNORECASE,
)

_NO_BUSINESS_FACTS_SYSTEM_PROMPT = (
    "你是某电商平台的智能客服助手，性格友善、专业、简洁。"
    "你可以帮助用户：查询订单状态和发货时间、查询物流信息、"
    "查询已提交的售后申请进度、处理退款/换货/商品损坏等售后申请。"
    "严格规则："
    "1) 你现在只是在打招呼或引导用户说明需求，绝不能在这类回复中给出任何"
    "退款结果、批准状态、金额或具体订单信息，这些必须走系统正式流程；"
    "2) 不要编造任何订单、物流或政策信息；"
    "3) 回复控制在1到2句话以内，自然口语化，不要用生硬的客服模板腔；"
    "4) 如果用户只是打招呼或寒暄，简短回应并顺带说明能帮忙查订单/物流/售后"
    "或办理退换货；"
    "5) 如果用户的话你确实无法判断具体诉求，礼貌地请他说明是要查订单、"
    "物流，还是处理退款换货。"
)

_FALLBACK_REPLIES = {
    "Greeting": "您好，请问需要查询订单、物流，还是处理退款换货？",
    "Unknown": (
        "我还不能确定您的需求。请说明您要查询订单、物流、售后进度，"
        "还是申请退款或换货。"
    ),
}


def _generate_conversational_reply(user_message: str, intent: str) -> str:
    """Use the LLM to reply naturally when there is no business branch to run.

    No business facts are at stake here (no order/amount/decision to protect),
    so this generates freely instead of the guarded template+polish pattern
    used for decision-bearing responses in response_agent.py.
    """
    try:
        return chat(
            f"用户消息：{user_message}\n\n请生成合适的客服回复。",
            system_prompt=_NO_BUSINESS_FACTS_SYSTEM_PROMPT,
            temperature=0.4,
        )
    except Exception:
        return _FALLBACK_REPLIES[intent]


def _classify_with_keywords(user_message: str) -> str | None:
    if any(word in user_message for word in ("转人工", "人工客服", "找人工", "真人客服")):
        return "HumanHandoff"
    if any(word in user_message for word in (
        "撤销申请", "取消申请", "撤销售后", "取消售后", "结束申请",
        "我不退了", "不想退了", "我不换了", "不想换了", "算了不退",
        "算了你结束吧", "算了结束吧",
    )):
        return "WithdrawServiceRequest"
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
        "想退了",
        "想退掉",
        "我要退了",
        "我要退掉",
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

    # A greeting word alone is not conclusive — "你好 我要退货" contains "你好"
    # but is not a greeting, and the after-sales phrasing that follows it
    # won't always match the fixed action-word list above (natural phrasing
    # varies too much for that). Returning a firm intent here would skip the
    # LLM fallback entirely (the caller only invokes it when this function
    # returns None), silently misrouting a real request as small talk. Only
    # short-circuit when the whole message is just the greeting itself, with
    # nothing else after it worth classifying.
    stripped = _GREETING_PATTERN.sub("", user_message).strip(" ，。！？!?、~～")
    if _GREETING_PATTERN.search(user_message) and not stripped:
        return "Greeting"

    return None


_CONFIRMATION_WORDS = {
    "需要",
    "好的",
    "好",
    "可以",
    "帮我催一下",
    "催一下",
    "麻烦催一下",
}

# What query_agent.py can silently arm as conversation_context.pending_action,
# and what each one turns into once the customer confirms it.
_PENDING_OFFERS = {
    "OfferExpediteShipment": {
        "description": "需要帮您催一下发货吗",
        "confirmed_action": "ExpediteShipmentConfirmed",
        "intent": "ExpediteShipmentConfirmation",
    },
    "OfferLogisticsChase": {
        "description": "需要帮您催一下物流吗",
        "confirmed_action": "LogisticsChaseConfirmed",
        "intent": "LogisticsChaseConfirmation",
    },
    "OfferHumanHandoff": {
        "description": "需要帮您转人工吗",
        "confirmed_action": "HumanHandoffConfirmed",
        "intent": "HumanHandoff",
    },
}


def _format_recent_history(
    history: list[dict[str, str]] | None,
    turns: int = 3,
) -> str:
    """Format the last few dialogue turns for a classification prompt.

    Casual follow-ups ("咋还没发", "我催一下") are frequently ambiguous
    without knowing what was just discussed; a bare current-message
    classifier has no way to resolve that.
    """
    if not history:
        return ""
    recent = history[-(turns * 2):]
    if not recent:
        return ""
    lines = [
        f"{'用户' if turn.get('role') == 'user' else 'Agent'}：{turn.get('content', '')}"
        for turn in recent
    ]
    return "最近对话（仅供理解上下文，不是要分类的内容）：\n" + "\n".join(lines) + "\n\n"


def _classify_yes_no_confirmation(
    user_message: str,
    question_description: str,
    history: list[dict[str, str]] | None = None,
) -> bool:
    """Whether this reply affirmatively answers a pending yes/no question.

    A fixed keyword set only matches exact phrasing ("我催一下" != "催一下"),
    and misses replies that bundle a "yes" with extra detail in the same
    breath (e.g. "对的 这个商品有问题" confirming a reapplication prompt
    while also stating the reason). Anything not caught by the fast keyword
    path falls through to a scoped LLM call rather than silently missing the
    confirmation. Shared across every pending yes/no prompt in this router —
    same shape, just a different question being confirmed.
    """
    result = chat_json(
        _format_recent_history(history)
        + f"用户消息：{user_message}\n\n用户是否在肯定答复这个问题？",
        system_prompt=(
            f"你在判断客户是否对系统刚才提出的问题'{question_description}'做出"
            "肯定确认。只输出 JSON，字段为 confirmed，取值只能是 true 或 false。"
            "只要客户明确表示同意/是的/对的/需要/可以/麻烦处理等肯定态度就是 true，"
            "即使客户在肯定的同时补充了其他信息（比如说明原因、提供细节）也算 true；"
            "只有客户明确否定、犹豫、答非所问，或意思完全不清楚时才是 false。"
        ),
        temperature=0.0,
    )
    return result.get("confirmed") is True


def _classify_pending_offer_confirmation(
    user_message: str,
    offer_description: str,
    history: list[dict[str, str]] | None = None,
) -> bool:
    return _classify_yes_no_confirmation(user_message, offer_description, history)


def _classify_pending_offer_decline(
    user_message: str,
    offer_description: str,
    history: list[dict[str, str]] | None = None,
) -> bool:
    """Whether this reply is declining a pending offer (not just failing to confirm it).

    A generic negation like "算了我不要了" or "不用了" carries no fixed intent
    of its own — it only means "cancel that thing you just offered" in the
    context of a pending yes/no question. Without this check, a decline that
    isn't one of a handful of exact phrases (see the withdraw-request keyword
    list) falls through to the generic classifier, which has no idea an offer
    is pending and can guess an unrelated intent (e.g. WithdrawServiceRequest
    when there is nothing open to withdraw). A reply that is neither a
    confirmation nor a decline (e.g. the customer ignores the offer and asks
    something else) must return False here so it still falls through normally.
    """
    result = chat_json(
        _format_recent_history(history)
        + f"用户消息：{user_message}\n\n用户是否在拒绝或不需要这个提议？",
        system_prompt=(
            f"你在判断客户是否在拒绝系统刚才提出的'{offer_description}'这个提议，"
            "或表示不需要/算了/不用了。只输出 JSON，字段为 declined，取值只能是 true 或 false。"
            "只有客户明确表示不需要/拒绝/算了/不用了等否定态度时才是 true；"
            "如果客户在肯定、问别的问题，或意思不清楚，都是 false。"
        ),
        temperature=0.0,
    )
    return result.get("declined") is True


def _classify_with_llm(
    user_message: str,
    history: list[dict[str, str]] | None = None,
) -> str:
    result = chat_json(
        (
            _format_recent_history(history)
            + "请将下面的用户消息分类为 JSON，唯一字段为 conversation_intent："
            f"\n\n{user_message}"
        ),
        system_prompt=(
            "你是电商客服消息路由器，只负责分类，不回答问题。"
            "如果提供了最近对话，可以用来理解当前这句话的上下文（比如它承接的"
            "是哪个话题），但分类结果只针对最后这一条用户消息，不要对历史消息分类。"
            "conversation_intent 只能是：OrderStatusQuery（是否发货或签收）、"
            "ShippingTimeQuery（询问什么时候或多久发货）、"
            "LogisticsQuery（快递位置、时效或异常）、AfterSalesStatusQuery"
            "（查询已有退款、退货、换货申请进度）、AfterSalesRequest"
            "（提出退款、退货、换货或商品问题处理请求）、WithdrawServiceRequest"
            "（客户明确表示撤销、取消或不再继续当前售后申请）、Greeting"
            "（打招呼、寒暄、闲聊，没有具体业务诉求）、Unknown"
            "（以上都不是，且无法判断具体诉求）。"
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

    dialogue_history = state.get("dialogue_history")

    normalized_message = user_message.strip(" ，。！？!?、")
    if context.get("pending_reapplication_confirmation"):
        explicit_no = (
            normalized_message in {"不", "不用", "不需要", "否", "取消", "算了", "先不申请"}
            or any(phrase in normalized_message for phrase in ("不重新申请", "不重新发起", "先不申请", "暂时不申请"))
        )
        if explicit_no:
            context.pop("pending_reapplication_confirmation", None)
            context.pop("pending_action", None)
            context.pop("pending_service_request", None)
            return {
                "conversation_context": context,
                "conversation_result": {
                    "status": "Resolved",
                    "conversation_intent": "AfterSalesRequest",
                    "matched_by": "reapplication_cancelled",
                    "order_id": context.get("active_order_id"),
                    "request_id": None,
                },
                "customer_response": "好的，本次不重新发起申请。之前撤销的申请仍会保留在历史记录中。",
            }

        # A bare "是的"/"对的" hits the fast path; a reply that bundles the
        # confirmation with extra detail ("对的 这个商品有问题") does not
        # match any fixed phrase, so it falls through to an LLM judgement
        # rather than being silently treated as "not confirmed" and re-asked
        # forever. user_message (not normalized_message) is passed through so
        # the bundled detail ("这个商品有问题") stays in pending_service_request
        # via _extract_details downstream, instead of being discarded.
        reapplication_confirmed = (
            normalized_message in _CONFIRMATION_WORDS | {"是", "是的", "确认", "重新申请", "继续"}
            or any(phrase in normalized_message for phrase in ("重新申请", "重新发起", "确认发起", "继续申请"))
        )
        if not reapplication_confirmed:
            try:
                reapplication_confirmed = _classify_yes_no_confirmation(
                    user_message, "是否要重新发起一笔新的售后申请", dialogue_history
                )
            except Exception:
                reapplication_confirmed = False
        if reapplication_confirmed:
            context.pop("pending_reapplication_confirmation", None)
            context["reapplication_confirmed_for_order"] = context.get("active_order_id")
            context["pending_action"] = "CreateServiceRequest"
            context["last_intent"] = "AfterSalesRequest"
            return {
                "conversation_context": context,
                "conversation_result": {
                    "status": "Resolved",
                    "conversation_intent": "AfterSalesRequest",
                    "matched_by": "reapplication_confirmation",
                    "order_id": context.get("active_order_id"),
                    "request_id": None,
                },
            }
        # Neither a clear yes nor a clear no — re-ask instead of falling
        # through to the generic classifier below, which has misrouted an
        # unresolved "是否重新发起" turn into an unrelated intent before
        # (e.g. WithdrawServiceRequest) while leaving the pending flag set.
        return {
            "conversation_context": context,
            "conversation_result": {
                "status": "NeedsClarification",
                "conversation_intent": "AfterSalesRequest",
                "matched_by": "reapplication_unclear",
                "order_id": context.get("active_order_id"),
                "request_id": None,
            },
            "customer_response": (
                "抱歉，没能理解您的意思。请直接回复"
                "\"是\"重新发起一笔新的售后申请，或回复\"不需要\"保留原状态。"
            ),
        }

    pending_order_intent = context.get("pending_order_confirmation")
    if pending_order_intent:
        order_confirmed = normalized_message in (
            _CONFIRMATION_WORDS | {"是", "是的", "对", "对的", "就是这个"}
        )
        if not order_confirmed:
            try:
                order_confirmed = _classify_yes_no_confirmation(
                    user_message,
                    f"是否要咨询或申请售后的是订单 {context.get('active_order_id')}",
                    dialogue_history,
                )
            except Exception:
                order_confirmed = False
        if order_confirmed:
            context["order_context_confirmed"] = True
            context.pop("pending_order_confirmation", None)
            context["last_intent"] = pending_order_intent
            return {
                "conversation_context": context,
                "conversation_result": {
                    "status": "Resolved",
                    "conversation_intent": pending_order_intent,
                    "matched_by": "order_confirmation",
                    "order_id": context.get("active_order_id"),
                    "request_id": context.get("active_request_id"),
                },
            }
    pending_offer = _PENDING_OFFERS.get(context.get("pending_action"))
    if pending_offer:
        if normalized_message in _CONFIRMATION_WORDS:
            confirmed = True
        else:
            try:
                confirmed = _classify_pending_offer_confirmation(
                    user_message, pending_offer["description"], dialogue_history
                )
            except Exception:
                confirmed = False

        if confirmed:
            order_id = context.get("active_order_id")
            context["pending_action"] = pending_offer["confirmed_action"]
            context["last_intent"] = pending_offer["intent"]
            return {
                "conversation_context": context,
                "conversation_result": {
                    "status": "Resolved",
                    "conversation_intent": pending_offer["intent"],
                    "matched_by": "conversation_context",
                    "order_id": order_id,
                    "request_id": context.get("active_request_id"),
                },
            }

        declined = normalized_message in {
            "不", "不用", "不用了", "不需要", "算了", "不要了", "算了不要了", "否",
        }
        if not declined:
            try:
                declined = _classify_pending_offer_decline(
                    user_message, pending_offer["description"], dialogue_history
                )
            except Exception:
                declined = False
        if declined:
            context.pop("pending_action", None)
            return {
                "conversation_context": context,
                "conversation_result": {
                    "status": "Resolved",
                    "conversation_intent": "AfterSalesRequest",
                    "matched_by": "pending_offer_declined",
                    "order_id": context.get("active_order_id"),
                    "request_id": None,
                },
                "customer_response": "好的，暂时不处理，如果之后需要随时告诉我。",
            }

    # Explicit control/query intents always override stale form-filling state.
    # Otherwise a prior CreateServiceRequest draft can turn "我的售后进度呢"
    # into another request-type question, even when an active request exists.
    explicit_intent = _classify_with_keywords(user_message)
    if explicit_intent in {
        "AfterSalesStatusQuery",
        "WithdrawServiceRequest",
        "HumanHandoff",
    }:
        intent = explicit_intent
        matched_by = "keywords"
    elif (
        context.get("pending_action") == "CreateServiceRequest"
        or context.get("pending_service_request")
        or (
            context.get("last_intent") == "AfterSalesRequest"
            and explicit_order_id is not None
        )
    ):
        intent = "AfterSalesRequest"
        matched_by = "conversation_context"
    else:
        intent = explicit_intent
        matched_by = "keywords"

    if intent is None:
        try:
            intent = _classify_with_llm(user_message, dialogue_history)
            matched_by = "llm"
        except Exception:
            intent = "Unknown"
            matched_by = "fallback"

    order_id = explicit_order_id or context.get("active_order_id")
    request_id = explicit_request_id or context.get("active_request_id")

    # "Withdraw" only makes sense against an application that already exists.
    # This guard is scoped to the LLM path only: the keyword list above is a
    # curated set of explicit negations ("算了不退了") where "nothing to
    # withdraw" is the correct reply even with no active_request_id. The LLM
    # path is different — colloquial phrasing like "给我退掉吧" ("just refund
    # it") reads as withdrawal language to the classifier, but with no
    # active_request_id there is nothing submitted yet to withdraw; the
    # customer is describing a fresh need, not retracting one. Without this
    # guard the turn dead-ends on "没有可撤销的售后申请" instead of ever
    # creating the request the customer actually asked for.
    if matched_by == "llm" and intent == "WithdrawServiceRequest" and not request_id:
        intent = "AfterSalesRequest"

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

    previous_order_id = context.get("active_order_id")
    if explicit_order_id and previous_order_id and explicit_order_id != previous_order_id:
        context.pop("active_request_id", None)
        context.pop("pending_action", None)
        context.pop("pending_service_request", None)
    if order_id:
        context["active_order_id"] = order_id
    if request_id:
        context["active_request_id"] = request_id
    context["last_intent"] = intent

    if intent == "Unknown":
        status = "NeedsClarification"
    elif intent == "Greeting":
        status = "Chitchat"
    else:
        status = "Resolved"

    result = {
        "status": status,
        "conversation_intent": intent,
        "matched_by": matched_by,
        "order_id": order_id,
        "request_id": request_id,
    }

    update: CustomerServiceState = {
        "conversation_context": context,
        "conversation_result": result,
    }
    if intent in ("Unknown", "Greeting"):
        update["customer_response"] = _generate_conversational_reply(
            user_message, intent
        )
    return update
