from typing import Any
from typing_extensions import TypedDict

class CustomerServiceState(TypedDict, total=False):

    user_message: str
    conversation_context: dict[str, Any]
    conversation_result: dict[str, Any]
    resolver_result: dict[str, Any]
    intent_result: dict[str, Any]
    query_result: dict[str, Any]
    action_result: dict[str, Any]

    request_id: str
    service_request: dict[str, Any]
    order: dict[str, Any]
    customer: dict[str, Any]
    product: dict[str, Any]

    logistics: list[dict[str, Any]]
    submitted_evi: list[dict[str, Any]]

    errors: list[str]

    order_evidence: dict[str, Any]
    logistics_evidence: dict[str, Any]
    
    evidence_review: dict[str, Any]
    policy_evidence: dict[str, Any]
    gate_result: dict[str, Any]
    proposed_decision: dict[str, Any]
    reflection_result: dict[str, Any]
    final_result: dict[str, Any]
    customer_response: str
