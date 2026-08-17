from src.backend.graph.state import CustomerServiceState


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
    }

    proposed_decision = state.get("proposed_decision")

    if proposed_decision is not None:
        return {
            "final_result": {
                **base_result,
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
        }

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

        return {
            "final_result": {
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
        }

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
        handling_status = "SystemError"
        workflow_status = service_request.get("workflow_status", "Pending")
    else:
        current_errors = state.get("errors", [])
        return {
            "errors": current_errors
            + [f"Finalization 无法处理 Gate 状态: {gate_status}"],
        }

    return {
        "final_result": {
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
    }
