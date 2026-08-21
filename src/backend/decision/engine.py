from src.backend.decision.rule_engine import evaluate_decision
from src.backend.graph.state import CustomerServiceState


def decision_engine_node(state: CustomerServiceState) -> CustomerServiceState:
    """Produce a candidate decision through the declarative rule registry."""
    gate_result = state.get("gate_result")
    if gate_result is None or not gate_result.get("can_decide", False):
        return {
            "errors": state.get("errors", [])
            + ["Decision Engine 收到未通过 Completeness Gate 的请求"]
        }

    required_fields = (
        "service_request",
        "order_evidence",
        "logistics_evidence",
        "evidence_review",
        "policy_evidence",
        "product",
    )
    missing = [field for field in required_fields if state.get(field) is None]
    if missing:
        return {
            "errors": state.get("errors", [])
            + ["Decision Engine 缺少输入: " + ", ".join(missing)]
        }

    policy_ids = set(
        state["policy_evidence"].get("candidate_policy_ids", [])
    )
    proposed_decision = evaluate_decision(
        {
            "service_request": state["service_request"],
            "order_evidence": state["order_evidence"],
            "logistics_evidence": state["logistics_evidence"],
            "evidence_review": state["evidence_review"],
            "product": state["product"],
            "order_item": state.get("order_item"),
            "policy_ids": policy_ids,
        }
    )
    if proposed_decision is None:
        return {
            "errors": state.get("errors", [])
            + ["Decision Engine 未找到匹配的声明式裁决规则"]
        }

    proposed_decision["request_id"] = state["service_request"].get("request_id")
    return {"proposed_decision": proposed_decision}
