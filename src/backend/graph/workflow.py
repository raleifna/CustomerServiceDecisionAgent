from src.backend.graph.state import CustomerServiceState
from src.backend.llm import chat
from src.backend.repositories.mock_db_repository import (
    get_customer,
    get_order,
    get_product,
)
from src.backend.repositories.evidence_repository import get_evidence_for_decision
from src.backend.repositories.order_item_repository import (
    get_order_item,
    get_order_items,
    get_service_request_items,
)
from src.backend.repositories.service_request_repository import get_service_request
from src.backend.services.business_clock import get_business_clock
from src.backend.services.logistics_service import get_logistics_provider
from src.backend.services.tool_errors import ToolError
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
    query_agent_node,
    service_request_status_query_node,
)
from src.backend.actions.fulfillment_action import fulfillment_action_node
from src.backend.actions.logistics_chase_action import logistics_chase_action_node
from src.backend.actions.service_request_action import service_request_action_node
from src.backend.actions.human_handoff_action import human_handoff_action_node
from src.backend.actions.withdraw_service_request_action import withdraw_service_request_action_node
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
        "order_items": [],
        "request_items": [],
        "order_item": None,
        "customer": None,
        "product": None,
        "logistics": None,
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


_COMPRESS_AT = 16
_KEEP_RECENT = 6
_SUMMARY_PREFIX = "[之前对话摘要] "


def _compress_dialogue_history(
    history: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Roll old turns into one summary entry instead of truncating them away.

    Each pass regenerates a single consolidated summary (folding in any prior
    summary) rather than concatenating summaries onto each other forever, so
    the prompt this feeds into tool-calling agents stays bounded.
    """
    if len(history) <= _COMPRESS_AT:
        return history

    existing_summary = None
    rest = history
    first = history[0]
    if first.get("role") == "system" and first.get("content", "").startswith(
        _SUMMARY_PREFIX
    ):
        existing_summary = first["content"][len(_SUMMARY_PREFIX):]
        rest = history[1:]

    to_compress = rest[:-_KEEP_RECENT]
    keep = rest[-_KEEP_RECENT:]
    if not to_compress:
        return history

    transcript = "\n".join(
        f"{'用户' if turn['role'] == 'user' else 'Agent'}：{turn['content']}"
        for turn in to_compress
    )
    prompt_parts = []
    if existing_summary:
        prompt_parts.append(f"已有摘要：{existing_summary}")
    prompt_parts.append(f"新增对话：\n{transcript}")
    prompt_parts.append(
        "请把已有摘要和新增对话合并，用2-3句话总结关键信息"
        "（客户诉求、涉及的订单/申请、已经确认的事实），不要编造。"
    )

    try:
        summary = chat(
            "\n\n".join(prompt_parts),
            system_prompt="你是对话摘要助手，只输出摘要正文，不加其他说明。",
            temperature=0.0,
        )
    except Exception:
        # Don't let a summarization failure block the turn — plain
        # truncation is a safe (if lossier) degrade.
        return keep

    return [{"role": "system", "content": _SUMMARY_PREFIX + summary}, *keep]


def record_turn_node(state: CustomerServiceState) -> CustomerServiceState:
    """Append this turn to dialogue history, compressing old turns once it grows long."""
    user_message = state.get("user_message")
    if not user_message:
        return {}

    history = list(state.get("dialogue_history") or [])
    history.append({"role": "user", "content": user_message})
    customer_response = state.get("customer_response")
    if isinstance(customer_response, str) and customer_response:
        history.append({"role": "assistant", "content": customer_response})

    return {"dialogue_history": _compress_dialogue_history(history)}


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


def load_order_items_node(state: CustomerServiceState) -> CustomerServiceState:
    order = state.get("order")
    service_request = state.get("service_request")
    if order is None or service_request is None:
        return {"errors": state.get("errors", []) + ["加载订单项缺少订单或售后申请"]}
    order_items = get_order_items(order.get("order_id"))
    request_items = get_service_request_items(service_request.get("request_id"))
    if not order_items:
        return {"errors": state.get("errors", []) + ["订单没有可售后的订单项"]}
    if len(request_items) != 1:
        return {
            "order_items": order_items,
            "request_items": request_items,
            "errors": state.get("errors", [])
            + ["当前裁决流程要求一条售后申请明确关联一个订单项"],
        }
    target = get_order_item(request_items[0].get("order_item_id"))
    if target is None or target.get("order_id") != order.get("order_id"):
        return {
            "order_items": order_items,
            "request_items": request_items,
            "errors": state.get("errors", []) + ["售后申请项与订单项关联无效"],
        }
    return {
        "order_items": order_items,
        "request_items": request_items,
        "order_item": target,
    }
    


def load_product_node(state: CustomerServiceState,) -> CustomerServiceState:
    order_item = state.get("order_item")

    if order_item is None:
        cur_errors = state.get("errors", [])
        return {
            "errors": cur_errors + ["缺少order_item"]
        }
    product_id = order_item.get("product_id")

    if not product_id:
        cur_errors = state.get("errors", [])
        return {
            "errors": cur_errors + ["order_item缺少product_id"]
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
    """Populates state["logistics"] with the signal-wrapped shape from
    LogisticsProvider (Fixture or Postgres, APP_DATA_BACKEND-driven) --
    not a raw list of exception records anymore. See
    src/backend/services/logistics_service.py's module docstring for why
    tracking_stalled_over_72h/stalled_in_transit_over_72h report
    UNAVAILABLE on the Postgres backend regardless of what
    shipment_events actually contains."""
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

    as_of_date = get_business_clock().today()
    try:
        facts = get_logistics_provider().get_logistics_facts(order_id, as_of_date=as_of_date)
    except ToolError as exc:
        cur_errors = state.get("errors", [])
        return {
            "errors": cur_errors + [f"物流信息加载失败: {exc.code}"]
        }

    return {
        "logistics": facts.as_dict(),
    }

def load_evi_node(state: CustomerServiceState,) -> CustomerServiceState:
    request_id = state.get("request_id")
    if not request_id:
        cur_errors = state.get("errors", [])
        return {
            "errors": cur_errors + ["缺少request_id"]
        }
    
    submitted_evi = get_evidence_for_decision(request_id)

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
    if intent in {"OrderStatusQuery", "ShippingTimeQuery", "LogisticsQuery"}:
        return "query_agent"
    if intent == "AfterSalesStatusQuery":
        return "request_resolver"
    if intent == "AfterSalesRequest":
        if conversation_result.get("matched_by") in {
            "reapplication_cancelled",
            "pending_offer_declined",
        }:
            return "record_turn"
        if state.get("request_id") and not state.get("user_message"):
            return "request_resolver"
        return "service_request_action"
    if intent == "ExpediteShipmentConfirmation":
        return "fulfillment_action"
    if intent == "LogisticsChaseConfirmation":
        return "logistics_chase_action"
    if intent == "HumanHandoff":
        return "human_handoff_action"
    if intent == "WithdrawServiceRequest":
        return "withdraw_service_request_action"

    return "end"

def route_after_service_request_action(state: CustomerServiceState,) -> str:
    """If a service request was created (or already existed), continue
    straight into the same evidence-and-decision pipeline used for status
    queries instead of stopping at "created, pending". Cases with no
    evidence requirement (e.g. an unshipped-order cancellation) get a real
    decision in the same turn; cases still needing evidence/risk review
    correctly fall through to the same gate/reflection/escalation logic —
    this reuses that pipeline rather than special-casing an "obviously fine"
    shortcut that would skip risk checks like RISK-002.
    """
    action_result = state.get("action_result")
    if action_result and action_result.get("status") in {"Created", "Existing"}:
        return "load_order"

    return "record_turn"

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
    
    return "load_order_items"


def route_after_order_items(state: CustomerServiceState) -> str:
    return "load_customer" if state.get("order_item") is not None else "end"

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
    "query_agent",
    query_agent_node,
)

workflow_builder.add_node(
    "service_request_status_query",
    service_request_status_query_node,
)

workflow_builder.add_node(
    "record_turn",
    record_turn_node,
)

workflow_builder.add_node(
    "fulfillment_action",
    fulfillment_action_node,
)

workflow_builder.add_node(
    "logistics_chase_action",
    logistics_chase_action_node,
)

workflow_builder.add_node(
    "service_request_action",
    service_request_action_node,
)
workflow_builder.add_node("human_handoff_action", human_handoff_action_node)
workflow_builder.add_node("withdraw_service_request_action", withdraw_service_request_action_node)

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
        "query_agent": "query_agent",
        "request_resolver": "request_resolver",
        "fulfillment_action": "fulfillment_action",
        "logistics_chase_action": "logistics_chase_action",
        "service_request_action": "service_request_action",
        "human_handoff_action": "human_handoff_action",
        "withdraw_service_request_action": "withdraw_service_request_action",
        "record_turn": "record_turn",
        "end": END,
    },
)

workflow_builder.add_edge(
    "query_agent",
    "record_turn",
)

workflow_builder.add_edge(
    "fulfillment_action",
    "record_turn",
)

workflow_builder.add_edge(
    "logistics_chase_action",
    "record_turn",
)
workflow_builder.add_edge("human_handoff_action", "record_turn")
workflow_builder.add_edge("withdraw_service_request_action", "record_turn")

workflow_builder.add_conditional_edges(
    "service_request_action",
    route_after_service_request_action,
    {
        "load_order": "load_order",
        "record_turn": "record_turn",
    },
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
workflow_builder.add_node("load_order_items", load_order_items_node)

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
    "record_turn",
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
        "load_order_items": "load_order_items",
        "end": END,
    },
)

workflow_builder.add_conditional_edges(
    "load_order_items",
    route_after_order_items,
    {"load_customer": "load_customer", "end": END},
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
    "record_turn",
)

workflow_builder.add_edge(
    "record_turn",
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
