"""Human handoff sessions and their live messages.

A handoff starts `Waiting` when the AI hands a conversation over (or `Active`
when a staff member opens one from a case), becomes `Active` once a staff
member accepts it, and ends `Ended`. A customer has at most one open
(Waiting/Active) handoff at a time; while it is open, the customer's chat
messages go to the human instead of the agent graph.

Postgres only: without DATABASE_URL every write raises LiveChatUnavailable
and reads return empty, so local fixture mode still works without live chat.
"""

from typing import Any
from uuid import uuid4

from src.backend.repositories.postgres_repository import get_database_url

OPEN_STATUSES = ("Waiting", "Active")
HANDOFF_FIELDS = """
    handoff_id, customer_id, order_id, request_id, reason, workflow_status,
    staff_id, conversation_id, accepted_at, ended_at, end_reason,
    last_activity_at, created_at, updated_at
"""


class LiveChatUnavailable(RuntimeError):
    """Live chat needs PostgreSQL."""


class LiveChatConflict(RuntimeError):
    """The handoff is not in a state that allows this operation."""


def _connect():
    database_url = get_database_url()
    if not database_url:
        raise LiveChatUnavailable("人工在线会话需要配置数据库")
    import psycopg
    from psycopg.rows import dict_row
    return psycopg.connect(database_url, row_factory=dict_row)


def _iso(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {key: value.isoformat() if hasattr(value, "isoformat") else value for key, value in dict(row).items()}


def _system_message(cursor, handoff_id: str, content: str) -> None:
    cursor.execute(
        "INSERT INTO live_messages (handoff_id, sender, sender_id, content) VALUES (%s, 'system', NULL, %s)",
        (handoff_id, content),
    )


def open_handoff_for_customer(customer_id: str) -> dict[str, Any] | None:
    if not get_database_url():
        return None
    with _connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            f"SELECT {HANDOFF_FIELDS} FROM human_handoffs WHERE customer_id = %s "
            "AND workflow_status = ANY(%s) ORDER BY created_at DESC LIMIT 1",
            (customer_id, list(OPEN_STATUSES)),
        )
        return _iso(cursor.fetchone())


def create_or_reuse_handoff(
    *,
    customer_id: str,
    reason: str,
    order_id: str | None = None,
    request_id: str | None = None,
    conversation_id: str | None = None,
    staff_id: str | None = None,
) -> dict[str, Any]:
    """Open a handoff, or return the customer's already-open one (a customer
    is never queued twice). With staff_id it starts Active, assigned to them."""
    with _connect() as connection, connection.cursor() as cursor:
        # Serialize per customer so two concurrent requests cannot both create one.
        cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"handoff:{customer_id}",))
        cursor.execute(
            f"SELECT {HANDOFF_FIELDS} FROM human_handoffs WHERE customer_id = %s "
            "AND workflow_status = ANY(%s) ORDER BY created_at DESC LIMIT 1",
            (customer_id, list(OPEN_STATUSES)),
        )
        existing = cursor.fetchone()
        if existing is not None:
            return _iso(existing)
        handoff_id = f"HOF-{uuid4().hex}"
        status = "Active" if staff_id else "Waiting"
        cursor.execute(
            f"""INSERT INTO human_handoffs
                   (handoff_id, customer_id, order_id, request_id, reason, workflow_status,
                    staff_id, conversation_id, accepted_at, last_activity_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s,
                       CASE WHEN %s::text IS NULL THEN NULL ELSE CURRENT_TIMESTAMP END, CURRENT_TIMESTAMP)
               RETURNING {HANDOFF_FIELDS}""",
            (handoff_id, customer_id, order_id, request_id, reason, status, staff_id, conversation_id, staff_id),
        )
        row = cursor.fetchone()
        _system_message(
            cursor, handoff_id,
            f"人工客服 {staff_id} 发起了会话" if staff_id else "已为您转接人工客服，正在排队，请稍候",
        )
        return _iso(row)


def get_handoff(handoff_id: str) -> dict[str, Any] | None:
    if not get_database_url():
        return None
    with _connect() as connection, connection.cursor() as cursor:
        cursor.execute(f"SELECT {HANDOFF_FIELDS} FROM human_handoffs WHERE handoff_id = %s", (handoff_id,))
        return _iso(cursor.fetchone())


def list_handoffs(*, include_recent_ended: int = 10) -> list[dict[str, Any]]:
    """Open handoffs (Waiting first, oldest first) plus the latest ended ones,
    each with its last message and message count."""
    if not get_database_url():
        return []
    with _connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            f"""
            WITH chosen AS (
                (SELECT {HANDOFF_FIELDS} FROM human_handoffs WHERE workflow_status = ANY(%s))
                UNION ALL
                (SELECT {HANDOFF_FIELDS} FROM human_handoffs WHERE workflow_status = 'Ended'
                 ORDER BY ended_at DESC NULLS LAST LIMIT %s)
            )
            SELECT chosen.*,
                   last.content AS last_message, last.sender AS last_sender,
                   (SELECT count(*) FROM live_messages m WHERE m.handoff_id = chosen.handoff_id
                      AND m.sender = 'customer') AS customer_message_count
            FROM chosen
            LEFT JOIN LATERAL (
                SELECT content, sender FROM live_messages m
                WHERE m.handoff_id = chosen.handoff_id ORDER BY message_id DESC LIMIT 1
            ) last ON TRUE
            ORDER BY CASE chosen.workflow_status WHEN 'Waiting' THEN 0 WHEN 'Active' THEN 1 ELSE 2 END,
                     chosen.created_at
            """,
            (list(OPEN_STATUSES), include_recent_ended),
        )
        return [_iso(row) for row in cursor.fetchall()]


def list_messages(handoff_id: str, *, after_id: int = 0) -> list[dict[str, Any]]:
    if not get_database_url():
        return []
    with _connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT message_id, handoff_id, sender, sender_id, content, created_at FROM live_messages "
            "WHERE handoff_id = %s AND message_id > %s ORDER BY message_id",
            (handoff_id, after_id),
        )
        return [_iso(row) for row in cursor.fetchall()]


def add_message(handoff_id: str, *, sender: str, sender_id: str | None, content: str) -> dict[str, Any]:
    with _connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT workflow_status FROM human_handoffs WHERE handoff_id = %s FOR UPDATE", (handoff_id,))
        row = cursor.fetchone()
        if row is None or row["workflow_status"] not in OPEN_STATUSES:
            raise LiveChatConflict("该人工会话已结束")
        cursor.execute(
            "INSERT INTO live_messages (handoff_id, sender, sender_id, content) VALUES (%s, %s, %s, %s) "
            "RETURNING message_id, handoff_id, sender, sender_id, content, created_at",
            (handoff_id, sender, sender_id, content),
        )
        message = cursor.fetchone()
        cursor.execute(
            "UPDATE human_handoffs SET last_activity_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP "
            "WHERE handoff_id = %s",
            (handoff_id,),
        )
        return _iso(message)


def accept_handoff(handoff_id: str, staff_id: str) -> dict[str, Any]:
    """Waiting -> Active for this staff member. Re-accepting your own active
    session is a no-op; taking over someone else's is refused."""
    with _connect() as connection, connection.cursor() as cursor:
        cursor.execute(f"SELECT {HANDOFF_FIELDS} FROM human_handoffs WHERE handoff_id = %s FOR UPDATE", (handoff_id,))
        row = cursor.fetchone()
        if row is None:
            raise LiveChatConflict("会话不存在")
        if row["workflow_status"] == "Active" and row["staff_id"] == staff_id:
            return _iso(row)
        if row["workflow_status"] != "Waiting":
            raise LiveChatConflict("该会话已被其他客服接入或已结束")
        cursor.execute(
            f"""UPDATE human_handoffs SET workflow_status = 'Active', staff_id = %s,
                   accepted_at = CURRENT_TIMESTAMP, last_activity_at = CURRENT_TIMESTAMP,
                   updated_at = CURRENT_TIMESTAMP
               WHERE handoff_id = %s RETURNING {HANDOFF_FIELDS}""",
            (staff_id, handoff_id),
        )
        accepted = cursor.fetchone()
        _system_message(cursor, handoff_id, f"人工客服 {staff_id} 已接入，正在为您服务")
        return _iso(accepted)


def end_handoff(handoff_id: str, *, ended_by: str, reason: str) -> dict[str, Any]:
    with _connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            f"""UPDATE human_handoffs SET workflow_status = 'Ended', ended_at = CURRENT_TIMESTAMP,
                   end_reason = %s, updated_at = CURRENT_TIMESTAMP
               WHERE handoff_id = %s AND workflow_status = ANY(%s) RETURNING {HANDOFF_FIELDS}""",
            (reason, handoff_id, list(OPEN_STATUSES)),
        )
        ended = cursor.fetchone()
        if ended is None:
            raise LiveChatConflict("该人工会话已结束")
        labels = {"customer": "您已结束人工服务", "staff": "人工客服已结束本次服务", "timeout": "会话长时间无回复，已自动结束"}
        _system_message(cursor, handoff_id, f"{labels.get(ended_by, '人工服务已结束')}，后续由智能助手继续为您服务")
        return _iso(ended)


def changes_since(*, after_message_id: int, since_handoff_update: str | None, customer_id: str | None = None) -> dict[str, Any]:
    """Everything the live streams need in one round trip: new messages and
    handoffs whose state changed, optionally scoped to one customer."""
    if not get_database_url():
        return {"messages": [], "handoffs": [], "max_updated_at": since_handoff_update}
    customer_clause = "AND h.customer_id = %s" if customer_id else ""
    params: list[Any] = [after_message_id] + ([customer_id] if customer_id else [])
    with _connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            f"""SELECT m.message_id, m.handoff_id, m.sender, m.sender_id, m.content, m.created_at
                FROM live_messages m JOIN human_handoffs h ON h.handoff_id = m.handoff_id
                WHERE m.message_id > %s {customer_clause} ORDER BY m.message_id LIMIT 200""",
            params,
        )
        messages = [_iso(row) for row in cursor.fetchall()]
        handoff_params: list[Any] = []
        clauses = []
        if since_handoff_update:
            clauses.append("updated_at > %s")
            handoff_params.append(since_handoff_update)
        if customer_id:
            clauses.append("customer_id = %s")
            handoff_params.append(customer_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        cursor.execute(
            f"SELECT {HANDOFF_FIELDS} FROM human_handoffs {where} ORDER BY updated_at LIMIT 200",
            handoff_params,
        )
        handoffs = [_iso(row) for row in cursor.fetchall()]
    max_updated = max([h["updated_at"] for h in handoffs], default=since_handoff_update)
    return {"messages": messages, "handoffs": handoffs, "max_updated_at": max_updated}


def latest_message_id() -> int:
    if not get_database_url():
        return 0
    with _connect() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT COALESCE(max(message_id), 0) AS latest FROM live_messages")
        return int(cursor.fetchone()["latest"])
