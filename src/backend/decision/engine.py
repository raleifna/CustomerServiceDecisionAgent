from typing import Any

from src.backend.graph.state import CustomerServiceState


def decision_engine_node(
    state: CustomerServiceState,
) -> CustomerServiceState:
    """Produce a candidate business decision from verified facts and rules."""
    gate_result = state.get("gate_result")

    if gate_result is None or not gate_result.get("can_decide", False):
        current_errors = state.get("errors", [])
        return {
            "errors": current_errors
            + ["Decision Engine 收到未通过 Completeness Gate 的请求"],
        }

    service_request = state.get("service_request")
    order_evidence = state.get("order_evidence")
    logistics_evidence = state.get("logistics_evidence")
    evidence_review = state.get("evidence_review")
    policy_evidence = state.get("policy_evidence")
    product = state.get("product")

    required_inputs = {
        "service_request": service_request,
        "order_evidence": order_evidence,
        "logistics_evidence": logistics_evidence,
        "evidence_review": evidence_review,
        "policy_evidence": policy_evidence,
        "product": product,
    }
    missing_inputs = [
        name for name, value in required_inputs.items() if value is None
    ]

    if missing_inputs:
        current_errors = state.get("errors", [])
        return {
            "errors": current_errors
            + ["Decision Engine 缺少输入: " + ", ".join(missing_inputs)],
        }

    request_type = service_request.get("request_type")
    request_reason = service_request.get("request_reason")
    requested_quantity = service_request.get("requested_quantity")
    unit_price = product.get("price")
    policy_ids = set(policy_evidence.get("candidate_policy_ids", []))

    def build_decision(
        decision: str,
        confidence: float,
        rule_hits: list[str],
        reason_chain: list[str],
        approved_quantity: int | None = None,
        approved_amount: float | None = None,
    ) -> CustomerServiceState:
        proposed_decision: dict[str, Any] = {
            "request_id": service_request.get("request_id"),
            "decision": decision,
            "decision_confidence": confidence,
            "approved_quantity": approved_quantity,
            "approved_amount": approved_amount,
            "rule_hits": rule_hits,
            "reason_chain": reason_chain,
        }
        return {"proposed_decision": proposed_decision}

    def calculate_amount(quantity: int | None) -> float | None:
        if quantity is None or not isinstance(unit_price, (int, float)):
            return None
        return round(float(unit_price) * quantity, 2)

    if request_reason == "未发货取消订单" and "REFUND-010" in policy_ids:
        return build_decision(
            decision="Approve",
            confidence=0.99,
            rule_hits=["REFUND-010"],
            reason_chain=["订单已付款且尚未进入实际出库"],
            approved_quantity=requested_quantity,
            approved_amount=calculate_amount(requested_quantity),
        )

    if request_reason == "不想要了" and "REFUND-009" in policy_ids:
        return build_decision(
            decision="Reject",
            confidence=0.99,
            rule_hits=["REFUND-009"],
            reason_chain=["定制商品不适用无理由退货"],
        )

    if request_reason == "不想要了" and "REFUND-002" in policy_ids:
        return build_decision(
            decision="Reject",
            confidence=0.98,
            rule_hits=["REFUND-002"],
            reason_chain=["非质量退款申请距签收已超过30天且无例外"],
        )

    if request_reason == "不想要了" and "REFUND-001" in policy_ids:
        return build_decision(
            decision="Approve",
            confidence=0.98,
            rule_hits=["REFUND-001"],
            reason_chain=["非定制商品在签收后7天内申请无理由退款"],
            approved_quantity=requested_quantity,
            approved_amount=calculate_amount(requested_quantity),
        )

    if (
        request_reason == "错发商品"
        and order_evidence.get("product_match") is False
        and "REFUND-004" in policy_ids
    ):
        approved_amount = (
            calculate_amount(requested_quantity)
            if request_type in {"Refund", "PartialRefund"}
            else None
        )
        return build_decision(
            decision="Approve",
            confidence=0.99,
            rule_hits=["REFUND-004"],
            reason_chain=["仓库实际出库商品与客户订购商品不一致"],
            approved_quantity=requested_quantity,
            approved_amount=approved_amount,
        )

    confirmed_missing_quantity = order_evidence.get(
        "confirmed_missing_quantity"
    )
    if (
        request_reason == "少发商品"
        and isinstance(confirmed_missing_quantity, int)
        and confirmed_missing_quantity > 0
        and "REFUND-005" in policy_ids
    ):
        approved_quantity = min(
            requested_quantity,
            confirmed_missing_quantity,
        )
        is_partial_approval = requested_quantity > approved_quantity
        rule_hits = ["REFUND-005"]

        if is_partial_approval and "RISK-006" in policy_ids:
            rule_hits.append("RISK-006")

        return build_decision(
            decision=("PartialApprove" if is_partial_approval else "Approve"),
            confidence=0.99,
            rule_hits=rule_hits,
            reason_chain=[
                f"仓库记录确认少发 {confirmed_missing_quantity} 件",
                f"本次批准 {approved_quantity} 件",
            ],
            approved_quantity=approved_quantity,
            approved_amount=calculate_amount(approved_quantity),
        )

    if (
        request_reason == "未收到货"
        and logistics_evidence.get("has_unresolved_suspected_loss", False)
        and "SHIPPING-002" in policy_ids
    ):
        rule_hits = ["SHIPPING-002"]
        if "VIP-002" in policy_ids:
            rule_hits.append("VIP-002")

        return build_decision(
            decision="Approve",
            confidence=0.96,
            rule_hits=rule_hits,
            reason_chain=["物流系统存在尚未解决的疑似丢件记录"],
            approved_quantity=requested_quantity,
            approved_amount=calculate_amount(requested_quantity),
        )

    if (
        request_reason == "商品损坏"
        and logistics_evidence.get("has_shipping_damage", False)
        and "REFUND-006" in policy_ids
    ):
        return build_decision(
            decision="Approve",
            confidence=0.97,
            rule_hits=["REFUND-006", "SHIPPING-003"],
            reason_chain=["物流系统存在与订单一致的运输破损记录"],
            approved_quantity=requested_quantity,
            approved_amount=(
                calculate_amount(requested_quantity)
                if request_type in {"Refund", "PartialRefund"}
                else None
            ),
        )

    if (
        request_reason == "质量问题"
        and evidence_review.get("has_usable_evidence", False)
        and "REFUND-003" in policy_ids
    ):
        confidence = evidence_review.get("max_supporting_confidence")
        if not isinstance(confidence, (int, float)):
            confidence = 0.80

        return build_decision(
            decision="Approve",
            confidence=float(confidence),
            rule_hits=["REFUND-003"],
            reason_chain=list(evidence_review.get("supporting_facts", [])),
            approved_quantity=requested_quantity,
            approved_amount=(
                calculate_amount(requested_quantity)
                if request_type in {"Refund", "PartialRefund"}
                else None
            ),
        )

    current_errors = state.get("errors", [])
    return {
        "errors": current_errors
        + ["Decision Engine 未找到与 ReadyForDecision 状态匹配的裁决规则"],
    }
