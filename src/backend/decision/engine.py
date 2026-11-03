import logging
from typing import Any

from src.backend.decision.rule_engine import evaluate_decision
from src.backend.graph.state import CustomerServiceState

logger = logging.getLogger(__name__)

_REQUIRED_FIELDS = (
    "service_request",
    "order_evidence",
    "logistics_evidence",
    "evidence_review",
    "policy_evidence",
    "product",
)


def _rule_context(state: CustomerServiceState) -> dict[str, Any]:
    return {
        "service_request": state["service_request"],
        "order_evidence": state["order_evidence"],
        "logistics_evidence": state["logistics_evidence"],
        "evidence_review": state["evidence_review"],
        "product": state["product"],
        "order_item": state.get("order_item"),
        "policy_ids": set(state["policy_evidence"].get("candidate_policy_ids", [])),
    }


def decision_engine_node(state: CustomerServiceState) -> CustomerServiceState:
    """Produce a candidate decision through the declarative rule registry."""
    gate_result = state.get("gate_result")
    if gate_result is None or not gate_result.get("can_decide", False):
        return {
            "errors": state.get("errors", [])
            + ["Decision Engine 收到未通过 Completeness Gate 的请求"]
        }

    missing = [field for field in _REQUIRED_FIELDS if state.get(field) is None]
    if missing:
        return {
            "errors": state.get("errors", [])
            + ["Decision Engine 缺少输入: " + ", ".join(missing)]
        }

    proposed_decision = evaluate_decision(_rule_context(state))
    if proposed_decision is None:
        return {
            "errors": state.get("errors", [])
            + ["Decision Engine 未找到匹配的声明式裁决规则"]
        }

    proposed_decision["request_id"] = state["service_request"].get("request_id")
    return {"proposed_decision": proposed_decision}


def shadow_recommendation(state: CustomerServiceState) -> dict[str, Any] | None:
    """What the rule registry would decide if the gate had let it.

    Called only on the branches where the request did NOT get an automatic
    decision (forced human escalation, missing evidence, reflection). It is
    advisory: nothing executes it. Persisting it lets the workbench show the
    reviewer the AI's suggestion and lets us measure how often humans agree
    with the AI (services/ai_review_service.py). None when the inputs never
    got loaded or no rule matches -- "no suggestion" is a valid answer.
    """
    if any(state.get(field) is None for field in _REQUIRED_FIELDS):
        return None
    try:
        proposal = evaluate_decision(_rule_context(state))
    except Exception:
        logger.exception("Shadow rule evaluation failed")
        return None
    if proposal is None:
        return None
    return {
        "decision": proposal.get("decision"),
        "approved_quantity": proposal.get("approved_quantity"),
        "approved_amount": proposal.get("approved_amount"),
        "decision_confidence": proposal.get("decision_confidence"),
        "matched_rule_id": proposal.get("matched_rule_id"),
        "rule_hits": proposal.get("rule_hits", []),
        "reasons": proposal.get("reason_chain", []),
    }
