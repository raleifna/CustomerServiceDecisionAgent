from typing import Any

from src.backend.graph.state import CustomerServiceState
from src.backend.repositories.service_request_repository import (
    update_service_request_result,
)
from src.backend.repositories.decision_lifecycle_repository import (
    persist_decision_lifecycle,
)


def _persist(final_result: dict[str, Any]) -> None:
    """Write Finalization's outcome back to the service_requests row.

    This is the only place service_requests.workflow_status/decision are
    supposed to change (see docs/04_Architecture.md section 7). Without this,
    every decision the graph computes was previously discarded once the chat
    turn ended, so a later status query would still read the original
    'Pending' row and a human queue would have no state to filter on.
    """
    update_service_request_result(
        final_result["request_id"],
        workflow_status=final_result["workflow_status"],
        decision=final_result["decision"],
        approved_quantity=final_result["approved_quantity"],
        approved_amount=final_result["approved_amount"],
        decision_confidence=final_result["decision_confidence"],
        reasons=final_result["reasons"],
        rule_hits=final_result["rule_hits"],
        required_actions=final_result["required_actions"],
    )


def finalization_node(
    state: CustomerServiceState,
) -> CustomerServiceState:
    """Normalize decision and non-decision branches into one final result."""
    service_request = state.get("service_request")
    gate_result = state.get("gate_result")

    if service_request is None or gate_result is None:
        current_errors = state.get("errors", [])
        return {
            "errors": current_errors
            + ["Finalization 缺少 service_request 或 gate_result"],
        }

    base_result = {
        "request_id": service_request.get("request_id"),
        "order_id": service_request.get("order_id"),
        "customer_id": service_request.get("customer_id"),
        "request_type": service_request.get("request_type"),
        "request_reason": service_request.get("request_reason"),
        "order_item_id": (state.get("order_item") or {}).get("order_item_id"),
        "order_item_count": len(state.get("order_items") or []),
    }

    proposed_decision = state.get("proposed_decision")

    if proposed_decision is not None:
        final_result = {
            **base_result,
            "matched_rule_id": proposed_decision.get("matched_rule_id"),
            "handling_status": "DecisionCompleted",
            "workflow_status": "Closed",
            "decision": proposed_decision.get("decision"),
            "approved_quantity": proposed_decision.get(
                "approved_quantity"
            ),
            "approved_amount": proposed_decision.get("approved_amount"),
            "decision_confidence": proposed_decision.get(
                "decision_confidence"
            ),
            "rule_hits": proposed_decision.get("rule_hits", []),
            "reasons": proposed_decision.get("reason_chain", []),
            "required_actions": [],
        }
        lifecycle = persist_decision_lifecycle(
            final_result,
            input_snapshot={
                "service_request": state.get("service_request"),
                "order_item": state.get("order_item"),
                "request_items": state.get("request_items"),
                "order_evidence": state.get("order_evidence"),
                "logistics_evidence": state.get("logistics_evidence"),
                "evidence_review": state.get("evidence_review"),
                "policy_ids": (state.get("policy_evidence") or {}).get(
                    "candidate_policy_ids", []
                ),
            },
        )
        final_result["decision_id"] = lifecycle["decision_id"]
        final_result["workflow_status"] = lifecycle["workflow_status"]
        final_result["execution"] = lifecycle["execution"]
        if lifecycle["execution"]:
            final_result["required_actions"] = [
                lifecycle["execution"]["action_type"]
            ]
        return {"final_result": final_result}

    reflection_result = state.get("reflection_result")

    if reflection_result is not None:
        reflection_route = reflection_result.get("route")

        if reflection_route == "human_escalation":
            workflow_status = "EscalatedToHuman"
            handling_status = "HumanEscalation"
        elif reflection_route == "request_more_evidence":
            workflow_status = "NeedsReflection"
            handling_status = "NeedsMoreEvidence"
        elif reflection_route == "await_logistics_review":
            workflow_status = "NeedsReflection"
            handling_status = "AwaitingExternalVerification"
        else:
            workflow_status = "NeedsReflection"
            handling_status = "NeedsReflection"

        final_result = {
            **base_result,
            "handling_status": handling_status,
            "workflow_status": workflow_status,
            "decision": None,
            "approved_quantity": None,
            "approved_amount": None,
            "decision_confidence": None,
            "rule_hits": [],
            "reasons": reflection_result.get("reasons", []),
            "required_actions": reflection_result.get(
                "required_actions",
                [],
            ),
        }
        _persist(final_result)
        return {"final_result": final_result}

    gate_status = gate_result.get("status")

    if gate_status == "HumanEscalation":
        handling_status = "HumanEscalation"
        workflow_status = "EscalatedToHuman"
    elif gate_status == "NeedsMoreEvidence":
        handling_status = "NeedsMoreEvidence"
        workflow_status = "NeedsReflection"
    elif gate_status == "AwaitingEvidenceReview":
        handling_status = "AwaitingEvidenceReview"
        workflow_status = "NeedsReflection"
    elif gate_status == "SystemError":
        # Something is broken right now (e.g. missing policy data) — leave
        # whatever was already persisted alone rather than overwriting a
        # possibly-good prior state with a transient failure.
        return {
            "final_result": {
                **base_result,
                "handling_status": "SystemError",
                "workflow_status": service_request.get(
                    "workflow_status", "Pending"
                ),
                "decision": None,
                "approved_quantity": None,
                "approved_amount": None,
                "decision_confidence": None,
                "rule_hits": [],
                "reasons": gate_result.get("reasons", []),
                "required_actions": [],
            }
        }
    else:
        current_errors = state.get("errors", [])
        return {
            "errors": current_errors
            + [f"Finalization 无法处理 Gate 状态: {gate_status}"],
        }

    final_result = {
        **base_result,
        "handling_status": handling_status,
        "workflow_status": workflow_status,
        "decision": None,
        "approved_quantity": None,
        "approved_amount": None,
        "decision_confidence": None,
        "rule_hits": [],
        "reasons": gate_result.get("reasons", []),
        "required_actions": [],
    }
    _persist(final_result)
    return {"final_result": final_result}
