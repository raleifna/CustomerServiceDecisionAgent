from uuid import uuid4

from src.backend.graph.state import CustomerServiceState
from src.backend.repositories.postgres_repository import get_database_url


def human_handoff_action_node(state: CustomerServiceState) -> CustomerServiceState:
    context = dict(state.get("conversation_context") or {})
    # A handoff confirmed from a pending offer arrives with the customer's
    # bare "需要"/"是的" as user_message — the real reason was recorded when
    # the offer was made, not in this turn's message.
    reason = (
        context.pop("pending_human_handoff_reason", None)
        or state.get("user_message")
        or "客户要求转人工"
    )
    handoff_id = f"HOF-{uuid4().hex}"
    database_url = get_database_url()
    if database_url:
        import psycopg
        with psycopg.connect(database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """INSERT INTO human_handoffs
                       (handoff_id, customer_id, order_id, request_id, reason)
                       VALUES (%s, %s, %s, %s, %s)""",
                    (handoff_id, context.get("authenticated_customer_id"),
                     context.get("active_order_id"), context.get("active_request_id"),
                     reason),
                )
    context["active_handoff_id"] = handoff_id
    return {
        "conversation_context": context,
        "action_result": {"action": "CreateHumanHandoff", "status": "Created", "handoff_id": handoff_id},
        "customer_response": f"已为您接入人工客服，排队编号为 {handoff_id}。人工客服会继续查看当前订单和本次对话，您无需重复描述。",
    }
