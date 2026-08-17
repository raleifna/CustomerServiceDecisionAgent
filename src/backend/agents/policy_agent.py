from datetime import date

from src.backend.graph.state import CustomerServiceState
from src.backend.repositories.policy_repository import get_policy_rule


def policy_agent_node(
    state: CustomerServiceState,
) -> CustomerServiceState:
    service_request = state.get("service_request")
    order_evidence = state.get("order_evidence")
    logistics_evidence = state.get("logistics_evidence")
    evidence_review = state.get("evidence_review")

    missing_inputs = []

    if service_request is None:
        missing_inputs.append("service_request")
    if order_evidence is None:
        missing_inputs.append("order_evidence")
    if logistics_evidence is None:
        missing_inputs.append("logistics_evidence")
    if evidence_review is None:
        missing_inputs.append("evidence_review")

    if missing_inputs:
        current_errors = state.get("errors", [])
        missing_text = ", ".join(missing_inputs)
        return {
            "errors": current_errors
            + [f"Policy Agent 缺少输入: {missing_text}"],
        }

    candidate_policy_ids: list[str] = []
    selection_reasons: dict[str, list[str]] = {}

    def add_candidate(policy_id: str, reason: str) -> None:
        if policy_id not in candidate_policy_ids:
            candidate_policy_ids.append(policy_id)
            selection_reasons[policy_id] = []

        if reason not in selection_reasons[policy_id]:
            selection_reasons[policy_id].append(reason)

    request_type = service_request.get("request_type")
    request_reason = service_request.get("request_reason")
    requested_quantity = service_request.get("requested_quantity")

    days_since_delivery = order_evidence.get("days_since_delivery")
    is_customized = order_evidence.get("is_customized")
    is_delivered = order_evidence.get("is_delivered")
    product_match = order_evidence.get("product_match")
    quantity_match = order_evidence.get("quantity_match")
    confirmed_missing_quantity = order_evidence.get(
        "confirmed_missing_quantity"
    )
    ordered_quantity = order_evidence.get("ordered_quantity")
    amount = order_evidence.get("amount")
    vip_level = order_evidence.get("vip_level")
    refund_history_count = order_evidence.get("refund_history_count")

    has_logistics_exception = logistics_evidence.get(
        "has_logistics_exception",
        False,
    )
    has_shipping_damage = logistics_evidence.get(
        "has_shipping_damage",
        False,
    )
    has_suspected_loss = logistics_evidence.get(
        "has_suspected_loss",
        False,
    )
    has_unresolved_suspected_loss = logistics_evidence.get(
        "has_unresolved_suspected_loss",
        False,
    )
    has_stalled_over_72h = logistics_evidence.get(
        "has_stalled_over_72h",
        False,
    )
    is_in_transit_overdue = order_evidence.get(
        "is_in_transit_overdue",
        False,
    )

    if request_reason == "不想要了":
        if is_customized:
            add_candidate("REFUND-009", "定制商品申请无理由退款")
        elif isinstance(days_since_delivery, int) and days_since_delivery <= 7:
            add_candidate("REFUND-001", "非定制商品在签收后7天内申请无理由退款")

        if isinstance(days_since_delivery, int) and days_since_delivery > 30:
            add_candidate("REFUND-002", "无理由退款申请距签收已超过30天")

    non_quality_reasons = {
        "不想要了",
        "物流延误",
    }

    if (
        request_type == "Refund"
        and request_reason in non_quality_reasons
        and isinstance(days_since_delivery, int)
        and days_since_delivery > 30
    ):
        add_candidate("REFUND-002", "非质量退款申请距签收已超过30天")

    if request_reason == "质量问题":
        add_candidate("REFUND-003", "客户申请原因为质量问题")

    if request_reason == "错发商品":
        add_candidate("REFUND-004", "客户申请原因为错发商品")

    if request_reason == "少发商品":
        add_candidate("REFUND-005", "客户申请原因为少发商品")

    if request_reason == "商品损坏":
        if has_shipping_damage:
            add_candidate("REFUND-006", "物流系统存在运输破损记录")
        else:
            add_candidate("REFUND-008", "客户报告损坏但物流无运输破损记录")

    if request_reason == "未发货取消订单":
        add_candidate("REFUND-010", "客户申请取消尚未发货的已付款订单")

    shipping_related_reason = request_reason in {
        "未收到货",
        "物流延误",
        "商品损坏",
    }

    if shipping_related_reason:
        add_candidate("SHIPPING-001", "需要判断订单物流生命周期状态")

    if has_suspected_loss:
        add_candidate("SHIPPING-002", "物流系统存在疑似丢件记录")

    if has_shipping_damage:
        add_candidate("SHIPPING-003", "物流系统存在运输破损记录")

    if has_stalled_over_72h:
        add_candidate("SHIPPING-004", "物流系统存在滞留超72小时记录")

        if (
            request_type == "Refund"
            and isinstance(days_since_delivery, int)
            and days_since_delivery > 30
        ):
            add_candidate("REFUND-007", "超时退款与历史物流异常需要冲突处理")

    if shipping_related_reason and not has_logistics_exception:
        add_candidate("SHIPPING-005", "相关申请没有独立物流异常记录")

    if request_reason == "未收到货" and is_delivered:
        add_candidate("SHIPPING-006", "系统显示已签收但客户声称未收到")

    if (
        request_reason == "未收到货"
        and is_in_transit_overdue
        and not has_unresolved_suspected_loss
    ):
        add_candidate("SHIPPING-007", "配送已逾期但物流尚未确认丢件")

    if vip_level in {"Silver", "Gold", "Platinum"}:
        add_candidate("VIP-001", f"客户会员等级为 {vip_level}")
        add_candidate("VIP-003", "会员权益不能单独决定退款资格")

    if (
        vip_level == "Platinum"
        and request_reason == "未收到货"
        and has_unresolved_suspected_loss
    ):
        add_candidate("VIP-002", "Platinum 客户存在未解决的疑似丢件")

    risk_triggered = False
    conflict_detected = False

    if isinstance(refund_history_count, int) and refund_history_count >= 10:
        add_candidate("RISK-001", "客户历史成功退款次数达到风险阈值")
        risk_triggered = True

    if isinstance(amount, (int, float)) and amount >= 5000:
        add_candidate("RISK-002", "订单金额达到大额人工审核阈值")
        risk_triggered = True

    customer = state.get("customer")
    register_date_value = (
        customer.get("register_date") if customer is not None else None
    )
    request_date_value = service_request.get("request_date")

    if register_date_value and request_date_value:
        registration_age_days = (
            date.fromisoformat(request_date_value)
            - date.fromisoformat(register_date_value)
        ).days

        if (
            registration_age_days <= 30
            and refund_history_count == 0
            and isinstance(amount, (int, float))
            and amount >= 5000
        ):
            add_candidate("RISK-003", "新注册账号在30天内申请大额退款")
            risk_triggered = True

    if request_reason == "错发商品" and product_match is True:
        conflict_detected = True
        add_candidate("RISK-004", "客户错发主张与仓库出库商品记录冲突")

    if request_reason == "少发商品" and quantity_match is True:
        conflict_detected = True
        add_candidate("RISK-004", "客户少发主张与仓库足量出库记录冲突")

    if request_reason == "未收到货" and is_delivered:
        conflict_detected = True
        add_candidate("RISK-004", "客户未收到主张与系统签收记录冲突")

    if (
        isinstance(requested_quantity, int)
        and isinstance(ordered_quantity, int)
        and requested_quantity > ordered_quantity
    ):
        conflict_detected = True
        add_candidate("RISK-004", "客户申请数量超过订单数量")

    if (
        request_reason == "少发商品"
        and isinstance(requested_quantity, int)
        and isinstance(confirmed_missing_quantity, int)
        and confirmed_missing_quantity > 0
        and requested_quantity > confirmed_missing_quantity
    ):
        add_candidate("RISK-006", "申请数量超过仓库记录确认的少发数量")
        risk_triggered = True

    if risk_triggered or conflict_detected:
        add_candidate("RISK-005", "需要遵守风险规则的决策边界")
        add_candidate("REFUND-007", "基础售后规则与风险或事实冲突需要统一处理")

    if evidence_review.get("requires_human_review"):
        add_candidate("RISK-005", "申请证据明确要求人工审核")

    candidate_policies = []
    missing_policy_ids = []

    for policy_id in candidate_policy_ids:
        policy = get_policy_rule(policy_id)

        if policy is None:
            missing_policy_ids.append(policy_id)
        else:
            candidate_policies.append(policy)

    return {
        "policy_evidence": {
            "request_id": service_request.get("request_id"),
            "request_type": request_type,
            "request_reason": request_reason,
            "candidate_policy_ids": candidate_policy_ids,
            "candidate_policies": candidate_policies,
            "selection_reasons": selection_reasons,
            "missing_policy_ids": missing_policy_ids,
        }
    }
