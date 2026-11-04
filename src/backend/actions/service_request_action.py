import re
from datetime import date

from src.backend.agents.request_intent_agent import extract_after_sales_intent
from src.backend.actions.human_handoff_action import human_handoff_action_node
from src.backend.config.business_rules import load_business_rules
from src.backend.graph.state import CustomerServiceState
from src.backend.repositories.mock_db_repository import (
    get_order,
    get_orders_by_customer_id,
    get_product,
)
from src.backend.repositories.service_request_repository import (
    create_service_request,
    get_prior_rejected_request_count,
    get_recent_submission_stats,
)
from src.backend.repositories.order_item_repository import get_order_items


def _extract_quantity_with_regex(message: str) -> int | None:
    quantity_match = re.search(r"(\d+)\s*件", message)
    return int(quantity_match.group(1)) if quantity_match else None


def _special_issue_guidance(category: str | None) -> str:
    if category in {"数码", "家电"}:
        return "功能故障、质量问题或错发商品"
    if category in {"服饰", "鞋靴"}:
        return "商家错发款式或尺码、实物尺寸与商品描述明显不符，或商品存在质量问题"
    if category == "食品":
        return "商品变质、包装破损、少发或错发"
    return "商品损坏、质量问题、少发或错发"


def _extract_details(
    message: str,
    history: list[dict[str, str]] | None = None,
) -> dict:
    """Understand a customer's after-sales request from free text.

    Uses the same LLM semantic extraction as request_intent_agent.py so
    phrasings like "尺码不对" or "颜色不喜欢" are correctly bucketed as
    request_reason="不想要了" without needing an ever-growing keyword list.
    Falls back to a plain digit-based quantity regex when the LLM call fails,
    so the guided creation flow can still make progress offline.
    """
    try:
        extracted = extract_after_sales_intent(message, history)
    except Exception:
        extracted = {
            "request_type": None,
            "request_reason": None,
            "requested_quantity": None,
        }

    if extracted.get("requested_quantity") is None:
        extracted["requested_quantity"] = _extract_quantity_with_regex(message)

    # Deterministic minimum extraction keeps the guided flow usable when the
    # optional LLM is disabled. Never infer a reason from the word "退款".
    if extracted.get("request_type") is None:
        if "部分退款" in message:
            extracted["request_type"] = "PartialRefund"
        elif (
            "退款" in message
            or "退掉" in message
            or "退货" in message
            or "想退了" in message
            or "我要退了" in message
        ):
            extracted["request_type"] = "Refund"
        elif "换货" in message or "换一个" in message:
            extracted["request_type"] = "Exchange"
    if extracted.get("request_reason") is None and any(
        word in message for word in ("不喜欢", "不想要", "不合适", "选错了")
    ):
        extracted["request_reason"] = "不想要了"

    return extracted


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
    extracted = _extract_details(
        state.get("user_message", ""), state.get("dialogue_history")
    )
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

    if context.get("order_context_confirmed") is False:
        context["pending_order_confirmation"] = "AfterSalesRequest"
        return {
            "conversation_context": context,
            "customer_response": (
                f"确认一下，您要咨询或申请售后的是订单 {order_id} 吗？"
            ),
        }

    rules = load_business_rules()
    submission_window_days = 30
    max_submissions = int(
        rules["risk"]["max_after_sales_submissions_per_order_30_days"]
    )
    submission_stats = get_recent_submission_stats(
        order_id,
        customer_id,
        window_days=submission_window_days,
    )

    # This cap must be checked before the reapplication-confirmation prompt
    # below, and unconditionally — no matter how the customer answers a
    # pending confirmation, a 4th submission on the same order within the
    # window is never allowed through the automated flow. Checking it after
    # the confirmation step would let a customer talk their way past the cap
    # by simply saying "是的" to the reapplication prompt.
    if submission_stats["submission_count"] >= max_submissions:
        context.pop("pending_service_request", None)
        context.pop("pending_reapplication_confirmation", None)
        context.pop("reapplication_confirmed_for_order", None)
        # Don't auto-escalate — offer it, and only hand off once the customer
        # actually confirms. Declining just leaves the request unresolved
        # for now (same as declining any other pending offer).
        context["pending_action"] = "OfferHumanHandoff"
        context["pending_human_handoff_reason"] = (
            f"订单 {order_id} 在 {submission_window_days} 天内已提交"
            f" {submission_stats['submission_count']} 次售后申请，达到自动受理上限"
        )
        return {
            "conversation_context": context,
            "customer_response": (
                f"订单 {order_id} 在近 {submission_window_days} 天内已提交过 "
                f"{submission_stats['submission_count']} 次售后申请，已达到自动受理上限，"
                "暂时无法再自动受理新的申请。需要帮您转人工核实吗？"
            ),
        }

    reapplication_confirmed = (
        context.get("reapplication_confirmed_for_order") == order_id
    )
    if submission_stats["latest_was_withdrawn"] and not reapplication_confirmed:
        context.pop("pending_service_request", None)
        context["pending_reapplication_confirmation"] = True
        context["pending_action"] = "ConfirmReapplication"
        return {
            "conversation_context": context,
            "customer_response": (
                f"订单 {order_id} 最近一笔售后申请已由您主动撤销。"
                "请确认：您是否要重新发起一笔新的售后申请？"
                "新申请会重新校验售后资格，并重新填写类型、原因、数量和证据。"
            ),
        }

    if not pending.get("request_type"):
        context["pending_action"] = "CreateServiceRequest"
        return {
            "conversation_context": context,
            "customer_response": "请问您希望申请退款、部分退款，还是换货？",
        }

    if (
        pending.get("request_type") in {"Refund", "PartialRefund"}
        and order.get("shipped_date") is None
    ):
        # The order hasn't shipped — that's a fact we already have, and it
        # rules out every other reason (质量问题/商品损坏/少发/错发/未收到货
        # all presuppose something was actually shipped or received). This
        # overrides whatever reason the LLM extraction guessed, because a
        # phrase like "我不需要了" reads as generic "不想要了" in isolation —
        # correct in spirit, but the wrong label, and "不想要了" needs an
        # already-delivered order to match any policy. Left unconditional,
        # that guess would silently block the auto-fill below (reason
        # wouldn't be empty anymore) and the request would later hit a
        # decision with no matching policy at all.
        pending["request_reason"] = "未发货取消订单"
        context["pending_service_request"] = pending

    if not pending.get("request_reason"):
        context["pending_action"] = "CreateServiceRequest"
        return {
            "conversation_context": context,
            "customer_response": (
                "请简单说明申请原因，例如商品损坏、质量问题、少发、未收到货，"
                "或不想要了/尺码不合适等无理由退货情形。"
            ),
        }

    if pending.get("request_reason") == "不想要了" and order.get("delivered_date"):
        delivered_date = date.fromisoformat(order["delivered_date"])
        days_since_delivery = (date.today() - delivered_date).days
        if days_since_delivery > 7:
            product = get_product(order.get("product_id")) or {}
            guidance = _special_issue_guidance(product.get("category"))
            previous_denial = context.get("last_eligibility_denial") or {}
            is_repeat = (
                previous_denial.get("order_id") == order_id
                and previous_denial.get("request_reason") == "不想要了"
            )
            context.pop("pending_service_request", None)
            context.pop("pending_action", None)
            context["last_eligibility_denial"] = {
                "order_id": order_id,
                "request_reason": "不想要了",
                "days_since_delivery": days_since_delivery,
            }
            if is_repeat:
                response = (
                    f"如果您仍是因为个人不喜欢或不想要了申请退货，订单 {order_id} "
                    "已经超过七天无理由退货期限，处理结果不会改变。"
                    f"如果商品存在{guidance}，请告诉我具体情况，我们会按专项售后处理。"
                )
            else:
                response = (
                    f"很抱歉，订单 {order_id} 已签收 {days_since_delivery} 天，"
                    "已超过七天无理由退货期限，暂时无法为您办理本次无理由退货。"
                    f"若商品存在{guidance}，请说明实际问题，我们会按专项售后处理。"
                )
            return {
                "conversation_context": context,
                "customer_response": response,
            }

    order_items = get_order_items(order_id)
    if not order_items:
        return {"customer_response": "该订单缺少商品明细，暂时无法创建售后申请。"}
    order_item = None
    selected_item_id = pending.get("order_item_id")
    if selected_item_id:
        order_item = next(
            (item for item in order_items if item.get("order_item_id") == selected_item_id),
            None,
        )
    if order_item is None and len(order_items) == 1:
        order_item = order_items[0]
    if order_item is None and len(order_items) > 1:
        message = state.get("user_message", "").upper()
        matches = [
            item for item in order_items
            if any(
                isinstance(identifier, str) and identifier.upper() in message
                for identifier in (
                    item.get("order_item_id"), item.get("sku_id"), item.get("product_id")
                )
            )
        ]
        if len(matches) == 1:
            order_item = matches[0]
            pending["order_item_id"] = order_item["order_item_id"]
            context["pending_service_request"] = pending
    if order_item is None:
        context["pending_action"] = "SelectOrderItem"
        options = "、".join(
            f"{item['sku_name']}（SKU {item['sku_id']}）" for item in order_items
        )
        return {
            "conversation_context": context,
            "customer_response": (
                "该订单包含多个商品，请选择需要售后的商品："
                f"{options}。系统不会默认对整张订单发起售后。"
            ),
        }

    # Same eligibility pre-check as the 7-day window above, for customized
    # goods (REFUND-009): tell the customer up front instead of creating a
    # request only for the decision engine to reject it. Checked on the
    # selected item, since only one item of a multi-item order may be
    # customized. The decision rule stays as the second line of defence.
    item_product = get_product(order_item.get("product_id")) or {}
    if pending.get("request_reason") == "不想要了" and item_product.get("is_customized"):
        context.pop("pending_service_request", None)
        context.pop("pending_action", None)
        context["last_eligibility_denial"] = {
            "order_id": order_id,
            "request_reason": "不想要了",
            "customized": True,
        }
        return {
            "conversation_context": context,
            "customer_response": (
                f"很抱歉，「{order_item.get('sku_name') or '该商品'}」是定制商品，"
                "制作完成后不支持无理由退货，本次不会为您创建售后申请。"
                "如果商品存在质量问题、破损、错发或少发，请告诉我具体情况，我们会按专项售后为您处理。"
            ),
        }
    requested_quantity = pending.get("requested_quantity")
    ordered_quantity = order_item.get("quantity")
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

    request_reason = pending["request_reason"]
    if request_reason in {"质量问题", "商品损坏"}:
        prior_rejections = get_prior_rejected_request_count(
            order_item["order_item_id"], request_reason
        )
        if prior_rejections >= 1:
            context.pop("pending_service_request", None)
            context.pop("pending_action", None)
            handoff = human_handoff_action_node({
                **state,
                "conversation_context": context,
                "user_message": (
                    f"订单 {order_id} 商品 {order_item['order_item_id']} 的"
                    f"「{request_reason}」申请此前已因证据不足被拒绝过，"
                    "客户再次提交同一问题"
                ),
            })
            handoff["customer_response"] = (
                f"该商品此前的「{request_reason}」申请已因材料不足被驳回过一次，"
                "为避免反复审核，本次将直接转交人工客服核实处理，不会再走自动审核流程。"
            )
            return handoff

    try:
        service_request, created = create_service_request(
            order_id=order_id,
            customer_id=customer_id,
            request_type=pending["request_type"],
            request_reason=pending["request_reason"],
            requested_quantity=requested_quantity,
            order_item=order_item,
            order_item_count=len(order_items),
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
        message = f"已为订单 {order_id} 创建售后申请 {request_id}，当前状态为待处理。"
        context.pop("reapplication_confirmed_for_order", None)
        if pending["request_reason"] in {"质量问题", "商品损坏"}:
            message += (
                "请继续提交清晰的图片或视频证据；"
                "申请不会仅凭文字描述自动批准。"
            )
        elif pending["request_reason"] == "未发货取消订单":
            message += "订单尚未发货，无需提交证据，我们会尽快为您核实处理。"
        else:
            message += "我们会尽快核实并处理。"
    else:
        # "Same order, already have an open request" reads like an error if
        # it doesn't say what that request actually is — the customer has no
        # way to tell this apart from a bug without that context.
        status_labels = {
            "Pending": "待处理",
            "InReview": "审核中",
            "NeedsReflection": "复核中",
            "EscalatedToHuman": "已转人工审核",
        }
        status_label = status_labels.get(
            service_request.get("workflow_status"),
            service_request.get("workflow_status", "待处理"),
        )
        message = (
            f"订单 {order_id} 已经有一条{service_request.get('request_date', '')}"
            f"提交的{pending['request_reason']}申请（{request_id}），"
            f"当前状态为{status_label}，不需要重复提交，我们会尽快处理。"
        )

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
