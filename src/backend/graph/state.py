from typing import Any
from typing_extensions import TypedDict

class CustomerServiceState(TypedDict, total=False):

    user_message: str
    # = the LangGraph checkpointer's thread_id (see api/main.py's
    # graph_thread_id) -- the same identifier conversations.conversation_id
    # is keyed on, so agent_runs logging can tie a run back to a
    # conversation without minting a second id.
    conversation_id: str
    conversation_context: dict[str, Any]
    conversation_result: dict[str, Any]
    dialogue_history: list[dict[str, str]]
    resolver_result: dict[str, Any]
    intent_result: dict[str, Any]
    query_result: dict[str, Any]
    action_result: dict[str, Any]

    request_id: str
    service_request: dict[str, Any]
    order: dict[str, Any]
    order_items: list[dict[str, Any]]
    request_items: list[dict[str, Any]]
    order_item: dict[str, Any]
    customer: dict[str, Any]
    product: dict[str, Any]

    # LogisticsFacts.as_dict() -- signal-wrapped, not a raw exception list.
    # See src/backend/services/logistics_service.py.
    logistics: dict[str, Any]
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
