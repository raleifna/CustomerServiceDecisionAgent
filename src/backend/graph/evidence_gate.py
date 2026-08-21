from src.backend.graph.state import CustomerServiceState
from src.backend.decision.rule_engine import automatic_policy_ids


def evidence_completeness_gate_node(
    state: CustomerServiceState,
) -> CustomerServiceState:
    """Check whether a request can proceed to business decision-making."""
    required_inputs = (
        "service_request",
        "order_evidence",
        "logistics_evidence",
        "evidence_review",
        "policy_evidence",
    )
    missing_inputs = [
        field_name
        for field_name in required_inputs
        if state.get(field_name) is None
    ]

    existing_errors = state.get("errors", [])

    if existing_errors or missing_inputs:
        reasons = list(existing_errors)

        if missing_inputs:
            reasons.append(
                "缺少 Gate 必需输入: " + ", ".join(missing_inputs)
            )

        return {
            "gate_result": {
                "status": "SystemError",
                "route": "end",
                "can_decide": False,
                "reasons": reasons,
            }
        }

    service_request = state["service_request"]
    logistics_evidence = state["logistics_evidence"]
    evidence_review = state["evidence_review"]
    policy_evidence = state["policy_evidence"]

    request_reason = service_request.get("request_reason")
    policy_ids = set(
        policy_evidence.get("candidate_policy_ids", [])
    )
    missing_policy_ids = policy_evidence.get(
        "missing_policy_ids",
        [],
    )

    if missing_policy_ids:
        return {
            "gate_result": {
                "status": "SystemError",
                "route": "end",
                "can_decide": False,
                "reasons": [
                    "候选政策编号无法加载: "
                    + ", ".join(missing_policy_ids)
                ],
            }
        }

    if (
        evidence_review.get("requires_human_review", False)
        or "RISK-002" in policy_ids
        or "RISK-003" in policy_ids
    ):
        reasons = []

        if evidence_review.get("requires_human_review", False):
            reasons.append("申请证据状态要求人工审核")
        if "RISK-002" in policy_ids:
            reasons.append("命中大额申请强制人工规则 RISK-002")
        if "RISK-003" in policy_ids:
            reasons.append("命中新账号大额退款强制人工规则 RISK-003")

        return {
            "gate_result": {
                "status": "HumanEscalation",
                "route": "human_escalation",
                "can_decide": False,
                "reasons": reasons,
            }
        }

    if evidence_review.get("has_pending_evidence", False):
        return {
            "gate_result": {
                "status": "AwaitingEvidenceReview",
                "route": "await_review",
                "can_decide": False,
                "reasons": ["申请证据仍处于 Pending 状态"],
            }
        }

    quality_evidence_required = request_reason == "质量问题"
    # A carrier can observe tracking/parcel anomalies, but cannot establish
    # that the product inside is damaged. Product-damage claims always need
    # customer evidence or a human-verified equivalent.
    damage_evidence_required = request_reason == "商品损坏"
    evidence_required = (
        quality_evidence_required or damage_evidence_required
    )

    if (
        evidence_required
        and not evidence_review.get("has_usable_evidence", False)
    ):
        if evidence_review.get("has_rejected_evidence", False):
            reason = "已提交材料被拒绝，当前没有可用于裁决的证据"
        elif not evidence_review.get("evidence_available", False):
            reason = "商品问题申请缺少最低要求的证据"
        else:
            reason = "当前材料尚未形成可用于裁决的支持证据"

        return {
            "gate_result": {
                "status": "NeedsMoreEvidence",
                "route": "request_more_evidence",
                "can_decide": False,
                "reasons": [reason],
            }
        }

    reflection_reasons = []

    if "RISK-001" in policy_ids:
        reflection_reasons.append("命中高频退款客户复核规则 RISK-001")

    if "RISK-004" in policy_ids:
        reflection_reasons.append("客户陈述与系统事实冲突 RISK-004")

    if "REFUND-002" in policy_ids and "SHIPPING-004" in policy_ids:
        reflection_reasons.append("超时退款条款与历史物流异常同时适用")

    if "SHIPPING-007" in policy_ids:
        reflection_reasons.append("配送逾期但物流尚未确认丢件")

    if (
        "REFUND-008" in policy_ids
        and evidence_review.get("has_machine_reviewed_evidence", False)
        and not evidence_review.get("has_verified_evidence", False)
    ):
        reflection_reasons.append("机器审核只能确认损坏现象，不能确认责任原因")

    if (
        "REFUND-003" in policy_ids
        and evidence_review.get("has_machine_reviewed_evidence", False)
        and not evidence_review.get("has_verified_evidence", False)
    ):
        reflection_reasons.append("机器审核只能确认商品外观异常，不能确认质量责任原因")

    if reflection_reasons:
        return {
            "gate_result": {
                "status": "NeedsReflection",
                "route": "reflection",
                "can_decide": False,
                "reasons": reflection_reasons,
            }
        }

    # A complete evidence bundle is not the same as an applicable automatic
    # decision rule.  Keep uncovered/expired combinations out of the Decision
    # Engine instead of turning a normal business boundary into SystemError.
    automatic_decision_policies = automatic_policy_ids()
    if not policy_ids.intersection(automatic_decision_policies):
        return {
            "gate_result": {
                "status": "NeedsReflection",
                "route": "reflection",
                "can_decide": False,
                "reasons": ["当前事实不满足任何可自动裁决的政策条款"],
            }
        }

    return {
        "gate_result": {
            "status": "ReadyForDecision",
            "route": "decision",
            "can_decide": True,
            "reasons": ["企业事实、申请证据和候选政策已满足裁决条件"],
        }
    }
