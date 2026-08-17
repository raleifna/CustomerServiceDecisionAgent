from src.backend.graph.state import CustomerServiceState
from src.backend.repositories.mock_db_repository import (
    get_customer,
    get_order,
    get_product,
    get_logistics,
    get_evi,
)
from src.backend.repositories.service_request_repository import get_service_request
from src.backend.agents.order_agent import order_agent_node
from src.backend.agents.logistics_agent import logistics_agent_node
from src.backend.agents.evidence_agent import evidence_agent_node
from src.backend.agents.policy_agent import policy_agent_node
from src.backend.graph.evidence_gate import evidence_completeness_gate_node
from src.backend.decision.engine import decision_engine_node
from src.backend.agents.reflection_agent import reflection_agent_node
from src.backend.graph.finalization import finalization_node
from src.backend.agents.response_agent import response_agent_node
from src.backend.agents.request_resolver_agent import request_resolver_node
from src.backend.agents.request_intent_agent import request_intent_node
from src.backend.agents.conversation_router_agent import conversation_router_node
from src.backend.agents.query_agent import (
    logistics_query_node,
    order_status_query_node,
    service_request_status_query_node,
)
from src.backend.actions.fulfillment_action import fulfillment_action_node
from src.backend.actions.service_request_action import service_request_action_node
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph


def prepare_turn_node(state: CustomerServiceState) -> CustomerServiceState:
    """Clear derived data from the previous conversational turn."""
    if not state.get("user_message"):
        return {}

    return {
        "conversation_result": None,
        "resolver_result": None,
        "intent_result": None,
        "query_result": None,
        "action_result": None,
        "request_id": None,
        "service_request": None,
        "order": None,
        "customer": None,
        "product": None,
        "logistics": [],
        "submitted_evi": [],
        "errors": [],
        "order_evidence": None,
        "logistics_evidence": None,
        "evidence_review": None,
        "policy_evidence": None,
        "gate_result": None,
        "proposed_decision": None,
        "reflection_result": None,
        "final_result": None,
        "customer_response": None,
    }

def load_service_request_node(state: CustomerServiceState,) -> CustomerServiceState:
    request_id = state.get("request_id")
    if not request_id:
        cur_errors = state.get("errors", [])

        return {
            "errors": cur_errors + ["缺少request_id"]
        }
    service_request = get_service_request(request_id)

    if service_request is None:
        cur_errors = state.get("errors", [])
        return {
            "errors": cur_errors + [f"找不到Service Request: {request_id}"]
        }
    
    return {
        "service_request": service_request,
    }

def load_order_node(state: CustomerServiceState,) -> CustomerServiceState:
    service_request = state.get("service_request")

    if service_request is None:
        cur_errors = state.get("errors", [])
        return {
            "errors": cur_errors + ["缺少service_request"]
        }
    order_id = service_request.get("order_id")
    if not order_id:
        cur_errors = state.get("errors", [])
        return {
            "errors": cur_errors + ["service_request缺少order_id"]
        }
    order = get_order(order_id)
    if order is None:
        cur_errors = state.get("errors", [])
        return {
            "errors": cur_errors + [f"找不到Order: {order_id}"]
        }
    
    return {
        "order": order,
    }



def load_customer_node(state: CustomerServiceState,) -> CustomerServiceState:
    service_request = state.get("service_request")

    if service_request is None:
        cur_errors = state.get("errors", [])
        return {
            "errors": cur_errors + ["缺少service_request"]
        }
    customer_id = service_request.get("customer_id")
    if not customer_id:
        cur_errors = state.get("errors", [])
        return {
            "errors": cur_errors + ["service_request缺少customer_id"]
        }
    customer = get_customer(customer_id)

    if customer is None:
        cur_errors = state.get("errors", [])
        return {
            "errors": cur_errors + [f"找不到Customer: {customer_id}"]
        }
    
    return {
        "customer": customer,
    }
    


def load_product_node(state: CustomerServiceState,) -> CustomerServiceState:
    order = state.get("order")

    if order is None:
        cur_errors = state.get("errors", [])
        return {
            "errors": cur_errors + ["缺少order"]
        }
    product_id = order.get("product_id")

    if not product_id:
        cur_errors = state.get("errors", [])
        return {
            "errors": cur_errors + ["order缺少product_id"]
        }
    
    product = get_product(product_id)
    if product is None:
        cur_errors = state.get("errors", [])
        return {
            "errors": cur_errors + [f"找不到product: {product_id}"]
        }
    
    return {
        "product": product,
    }

def load_logistics_node(state: CustomerServiceState,) -> CustomerServiceState:
    order = state.get("order")
    if order is None:
        cur_errors = state.get("errors", [])
        return {
            "errors": cur_errors + ["缺少order"]
        }
    
    order_id = order.get("order_id")
    if not order_id:
        cur_errors = state.get("errors", [])
        return {
            "errors": cur_errors + ["order缺少order_id"]
        }
    
    logistics = get_logistics(order_id)

    return {
        "logistics":logistics,
    }

def load_evi_node(state: CustomerServiceState,) -> CustomerServiceState:
    request_id = state.get("request_id")
    if not request_id:
        cur_errors = state.get("errors", [])
        return {
            "errors": cur_errors + ["缺少request_id"]
        }
    
    submitted_evi = get_evi(request_id)
    
    return {
        "submitted_evi": submitted_evi
    }


def route_after_service_request(state: CustomerServiceState,) -> str:
    if state.get("service_request") is None:
        return "end"

    conversation_result = state.get("conversation_result", {})
    if conversation_result.get("conversation_intent") == "AfterSalesStatusQuery":
        return "service_request_status_query"

    return "request_intent"

def route_after_conversation_router(state: CustomerServiceState,) -> str:
    conversation_result = state.get("conversation_result")
    if conversation_result is None:
        return "end"

    intent = conversation_result.get("conversation_intent")
    if intent in {"OrderStatusQuery", "ShippingTimeQuery"}:
        return "order_status_query"
    if intent == "LogisticsQuery":
        return "logistics_query"
    if intent == "AfterSalesStatusQuery":
        return "request_resolver"
    if intent == "AfterSalesRequest":
        if state.get("request_id") and not state.get("user_message"):
            return "request_resolver"
        return "service_request_action"
    if intent == "ExpediteShipmentConfirmation":
        return "fulfillment_action"

    return "end"

def route_after_request_intent(state: CustomerServiceState,) -> str:
    intent_result = state.get("intent_result")

    if intent_result is not None and intent_result.get("status") == "Mismatch":
        return "end"

    return "load_order"

def route_after_request_resolver(state: CustomerServiceState,) -> str:
    resolver_result = state.get("resolver_result")

    if (
        resolver_result is not None
        and resolver_result.get("status") == "Resolved"
        and state.get("request_id")
    ):
        return "load_service_request"

    return "end"

def route_after_order(state:CustomerServiceState,) -> str:
    if state.get("order") is None:
        return "end"
    
    return "load_customer"

def route_after_customer(state:CustomerServiceState,) -> str:
    if state.get("customer") is None:
        return "end"
    
    return "load_product"


def route_after_evidence_gate(
    state: CustomerServiceState,
) -> str:
    gate_result = state.get("gate_result")

    if gate_result is None:
        return "end"

    return gate_result.get("route", "end")


workflow_builder = StateGraph(CustomerServiceState)

workflow_builder.add_node(
    "prepare_turn",
    prepare_turn_node,
)

workflow_builder.add_node(
    "conversation_router",
    conversation_router_node,
)

workflow_builder.add_node(
    "request_resolver",
    request_resolver_node,
)

workflow_builder.add_node(
    "load_service_request",
    load_service_request_node,
)

workflow_builder.add_node(
    "request_intent",
    request_intent_node,
)

workflow_builder.add_node(
    "order_status_query",
    order_status_query_node,
)

workflow_builder.add_node(
    "logistics_query",
    logistics_query_node,
)

workflow_builder.add_node(
    "service_request_status_query",
    service_request_status_query_node,
)

workflow_builder.add_node(
    "fulfillment_action",
    fulfillment_action_node,
)

workflow_builder.add_node(
    "service_request_action",
    service_request_action_node,
)

workflow_builder.add_edge(
    START,
    "prepare_turn",
)

workflow_builder.add_edge(
    "prepare_turn",
    "conversation_router",
)

workflow_builder.add_conditional_edges(
    "conversation_router",
    route_after_conversation_router,
    {
        "order_status_query": "order_status_query",
        "logistics_query": "logistics_query",
        "request_resolver": "request_resolver",
        "fulfillment_action": "fulfillment_action",
        "service_request_action": "service_request_action",
        "end": END,
    },
)

workflow_builder.add_edge(
    "order_status_query",
    END,
)

workflow_builder.add_edge(
    "logistics_query",
    END,
)

workflow_builder.add_edge(
    "fulfillment_action",
    END,
)

workflow_builder.add_edge(
    "service_request_action",
    END,
)

workflow_builder.add_conditional_edges(
    "request_resolver",
    route_after_request_resolver,
    {
        "load_service_request": "load_service_request",
        "end": END,
    },
)

workflow_builder.add_node(
    "load_order",
    load_order_node,
)

workflow_builder.add_node(
    "load_customer",
    load_customer_node,
)

workflow_builder.add_node(
    "load_product",
    load_product_node,
)

workflow_builder.add_node(
    "load_logistics",
    load_logistics_node,
)

workflow_builder.add_node(
    "load_evi",
    load_evi_node,
)

workflow_builder.add_node(
    "order_agent",
    order_agent_node,
)

workflow_builder.add_node(
    "logistics_agent",
    logistics_agent_node,
)

workflow_builder.add_node(
    "evidence_agent",
    evidence_agent_node,
)

workflow_builder.add_node(
    "policy_agent",
    policy_agent_node,
)

workflow_builder.add_node(
    "evidence_completeness_gate",
    evidence_completeness_gate_node,
)

workflow_builder.add_node(
    "decision_engine",
    decision_engine_node,
)

workflow_builder.add_node(
    "reflection_agent",
    reflection_agent_node,
)

workflow_builder.add_node(
    "finalization",
    finalization_node,
)

workflow_builder.add_node(
    "response_agent",
    response_agent_node,
)



workflow_builder.add_conditional_edges(
    "load_service_request",
    route_after_service_request,
    {
        "request_intent": "request_intent",
        "service_request_status_query": "service_request_status_query",
        "end": END,
    },
)

workflow_builder.add_edge(
    "service_request_status_query",
    END,
)

workflow_builder.add_conditional_edges(
    "request_intent",
    route_after_request_intent,
    {
        "load_order": "load_order",
        "end": END,
    },
)

workflow_builder.add_conditional_edges(
    "load_order",
    route_after_order,
    {
        "load_customer": "load_customer",
        "end": END,
    },
)

workflow_builder.add_conditional_edges(
    "load_customer",
    route_after_customer,
    {
        "load_product": "load_product",
        "end": END,
    },
)

workflow_builder.add_edge(
    "load_product",
    "load_logistics",
)

workflow_builder.add_edge(
    "load_logistics",
    "load_evi",
)

workflow_builder.add_edge(
    "load_evi",
    "order_agent",
)

workflow_builder.add_edge(
    "load_evi",
    "logistics_agent",
)

workflow_builder.add_edge(
    "load_evi",
    "evidence_agent",
)

workflow_builder.add_edge(
    ["order_agent", "logistics_agent", "evidence_agent"],
    "policy_agent",
)

workflow_builder.add_edge(
    "policy_agent",
    "evidence_completeness_gate",
)

workflow_builder.add_conditional_edges(
    "evidence_completeness_gate",
    route_after_evidence_gate,
    {
        "decision": "decision_engine",
        "reflection": "reflection_agent",
        "human_escalation": "finalization",
        "request_more_evidence": "finalization",
        "await_review": "finalization",
        "end": "finalization",
    },
)

workflow_builder.add_edge(
    "decision_engine",
    "finalization",
)

workflow_builder.add_edge(
    "reflection_agent",
    "finalization",
)

workflow_builder.add_edge(
    "finalization",
    "response_agent",
)

workflow_builder.add_edge(
    "response_agent",
    END,
)


customer_service_graph = workflow_builder.compile()

conversation_memory = InMemorySaver()
customer_service_conversation_graph = workflow_builder.compile(
    checkpointer=conversation_memory,
)


def compile_conversation_graph(checkpointer):
    """Compile the same workflow with a runtime-provided durable checkpointer."""
    return workflow_builder.compile(checkpointer=checkpointer)
