"""Time-based housekeeping that nobody should have to remember to do.

Run by scripts/workers/run_scheduler.py (the `scheduler` compose service).
Every job takes `now` explicitly so tests can move time, reads its
thresholds from business_rules.json ("automation"), and takes a
transaction-scoped advisory lock so several scheduler replicas never process
the same rows twice. Each state change is written to audit_logs as actor
"scheduler".

Jobs:
- expire_unshipped_returns: an approved no-reason return whose goods were
  never shipped back within `return_shipment_days` is closed (no refund).
- release_expired_leases: a human-review claim whose lease ran out goes back
  to the unassigned queue (row_version bumps, so a stale form cannot submit).
- end_idle_live_chats: an active live chat with no message for
  `live_chat_idle_minutes`, or a queued one never accepted within
  `live_chat_queue_timeout_hours`, is ended and the customer told so.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from uuid import uuid4

from src.backend.config.business_rules import load_business_rules
from src.backend.repositories.postgres_repository import get_database_url

logger = logging.getLogger(__name__)


def _automation() -> dict[str, float]:
    return load_business_rules()["automation"]


def _audit(cursor, action: str, resource_type: str, resource_id: str, details: dict[str, Any]) -> None:
    from psycopg.types.json import Json
    cursor.execute(
        """INSERT INTO audit_logs (audit_id, actor_id, actor_role, action, resource_type,
                                   resource_id, outcome, details)
           VALUES (%s, 'scheduler', 'system', %s, %s, %s, 'Succeeded', %s)""",
        (f"AUD-{uuid4().hex}", action, resource_type, resource_id, Json(details)),
    )


def _locked(cursor, name: str) -> bool:
    cursor.execute("SELECT pg_try_advisory_xact_lock(hashtext(%s)) AS locked", (f"scheduler:{name}",))
    return bool(cursor.fetchone()["locked"])


def _run(name: str, body: Callable[[Any], list[str]]) -> list[str]:
    database_url = get_database_url()
    if not database_url:
        return []
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(database_url, row_factory=dict_row) as connection, connection.cursor() as cursor:
        if not _locked(cursor, name):
            logger.info("Scheduler job %s is running elsewhere; skipped", name)
            return []
        return body(cursor)


def expire_unshipped_returns(now: datetime) -> list[str]:
    days = _automation()["return_shipment_days"]
    cutoff = now - timedelta(days=days)

    def body(cursor) -> list[str]:
        cursor.execute(
            """UPDATE return_orders SET workflow_status = 'Expired', updated_at = %s
               WHERE workflow_status = 'AwaitingShipment' AND created_at < %s
               RETURNING return_order_id, request_id""",
            (now, cutoff),
        )
        expired = cursor.fetchall()
        for row in expired:
            note = f"批准后超过 {days:g} 天未寄回商品，退货申请已自动关闭，未产生退款。如仍需退货请重新申请。"
            cursor.execute(
                """UPDATE service_requests
                   SET workflow_status = 'Closed', decision = 'NoActionRequired',
                       approved_quantity = NULL, approved_amount = NULL,
                       resolution_notes = %s, resolved_at = %s, required_actions = '[]'::jsonb,
                       updated_at = %s
                   WHERE request_id = %s AND workflow_status = 'ReturnAwaitingShipment'""",
                (note, now, now, row["request_id"]),
            )
            _audit(cursor, "ExpireUnshippedReturn", "ReturnOrder", row["return_order_id"],
                   {"request_id": row["request_id"], "return_shipment_days": days})
        return [row["return_order_id"] for row in expired]

    return _run("expire_unshipped_returns", body)


def release_expired_leases(now: datetime) -> list[str]:
    def body(cursor) -> list[str]:
        cursor.execute(
            """UPDATE service_requests
               SET assigned_to = NULL, lease_expires_at = NULL, row_version = row_version + 1,
                   updated_at = %s
               WHERE assigned_to IS NOT NULL AND lease_expires_at < %s
                 AND workflow_status IN ('EscalatedToHuman', 'NeedsReflection')
               RETURNING request_id""",
            (now, now),
        )
        released = [row["request_id"] for row in cursor.fetchall()]
        for request_id in released:
            _audit(cursor, "ReleaseExpiredLease", "ServiceRequest", request_id, {})
        return released

    return _run("release_expired_leases", body)


def end_idle_live_chats(now: datetime) -> list[str]:
    rules = _automation()
    idle_cutoff = now - timedelta(minutes=rules["live_chat_idle_minutes"])
    queue_cutoff = now - timedelta(hours=rules["live_chat_queue_timeout_hours"])

    def body(cursor) -> list[str]:
        cursor.execute(
            """UPDATE human_handoffs
               SET workflow_status = 'Ended', ended_at = %s, updated_at = %s,
                   end_reason = CASE workflow_status WHEN 'Active' THEN '长时间无回复，自动结束'
                                                     ELSE '排队超时未接入，自动结束' END
               WHERE (workflow_status = 'Active' AND last_activity_at < %s)
                  OR (workflow_status = 'Waiting' AND created_at < %s)
               RETURNING handoff_id, end_reason""",
            (now, now, idle_cutoff, queue_cutoff),
        )
        ended = cursor.fetchall()
        for row in ended:
            message = (
                "会话长时间无回复，已自动结束，后续由智能助手继续为您服务"
                if row["end_reason"].startswith("长时间")
                else "抱歉，当前人工客服繁忙未能及时接入，会话已结束。您可以继续向智能助手描述问题，或稍后再次转人工"
            )
            cursor.execute(
                "INSERT INTO live_messages (handoff_id, sender, sender_id, content, created_at) VALUES (%s, 'system', NULL, %s, %s)",
                (row["handoff_id"], message, now),
            )
            _audit(cursor, "EndIdleHandoff", "HumanHandoff", row["handoff_id"], {"reason": row["end_reason"]})
        return [row["handoff_id"] for row in ended]

    return _run("end_idle_live_chats", body)


JOBS: dict[str, Callable[[datetime], list[str]]] = {
    "expire_unshipped_returns": expire_unshipped_returns,
    "release_expired_leases": release_expired_leases,
    "end_idle_live_chats": end_idle_live_chats,
}


def run_all(now: datetime | None = None) -> dict[str, list[str]]:
    """Run every job once; a failing job is logged and does not stop the others."""
    moment = now or datetime.now(timezone.utc)
    results: dict[str, list[str]] = {}
    for name, job in JOBS.items():
        try:
            results[name] = job(moment)
        except Exception:
            logger.exception("Scheduler job %s failed", name)
            results[name] = []
    return results
