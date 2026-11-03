from typing import Any
from uuid import uuid4

from src.backend.repositories.postgres_repository import get_database_url


def get_return_order_for_request(request_id: str) -> dict[str, Any] | None:
    database_url = get_database_url()
    if not database_url:
        return None
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM return_orders WHERE request_id = %s", (request_id,))
            row = cursor.fetchone()
            return dict(row) if row else None


def _serialize(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    for key, value in item.items():
        if hasattr(value, "isoformat"):
            item[key] = value.isoformat()
    if item.get("refundable_amount") is not None:
        item["refundable_amount"] = float(item["refundable_amount"])
    return item


def list_return_orders(statuses: list[str] | None = None) -> list[dict[str, Any]]:
    """Return orders for the staff warehouse queue, oldest update first."""
    database_url = get_database_url()
    if not database_url:
        return []
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            if statuses:
                cursor.execute(
                    "SELECT * FROM return_orders WHERE workflow_status = ANY(%s) ORDER BY updated_at",
                    (statuses,),
                )
            else:
                cursor.execute("SELECT * FROM return_orders ORDER BY updated_at DESC LIMIT 200")
            return [_serialize(row) for row in cursor.fetchall()]


def submit_return_shipment(request_id: str, customer_id: str, carrier: str, tracking_number: str) -> dict[str, Any] | None:
    database_url = get_database_url()
    if not database_url:
        raise RuntimeError("退货流程需要配置 DATABASE_URL")
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """UPDATE return_orders SET workflow_status='InTransit', return_carrier=%s,
                   return_tracking_number=%s, shipped_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
                   WHERE request_id=%s AND customer_id=%s AND workflow_status='AwaitingShipment'
                   RETURNING *""", (carrier, tracking_number, request_id, customer_id),
            )
            row = cursor.fetchone()
            if row:
                cursor.execute("UPDATE service_requests SET workflow_status='ReturnInTransit', required_actions='[]'::jsonb, updated_at=CURRENT_TIMESTAMP WHERE request_id=%s", (request_id,))
            return dict(row) if row else None


def receive_return(return_order_id: str) -> dict[str, Any] | None:
    return _transition(return_order_id, "InTransit", "AwaitingInspection", "received_at")


def inspect_return(return_order_id: str, accepted: bool, notes: str | None) -> dict[str, Any] | None:
    database_url = get_database_url()
    if not database_url:
        raise RuntimeError("退货流程需要配置 DATABASE_URL")
    import psycopg
    from psycopg.rows import dict_row
    from psycopg.types.json import Json
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM return_orders WHERE return_order_id=%s FOR UPDATE", (return_order_id,))
            row = cursor.fetchone()
            if not row or row["workflow_status"] != "AwaitingInspection":
                return None
            if accepted:
                action_id = f"ACT-{uuid4().hex}"
                cursor.execute("SELECT decision_id FROM decision_records WHERE request_id=%s ORDER BY created_at DESC LIMIT 1", (row["request_id"],))
                decision_id = cursor.fetchone()["decision_id"]
                payload = {"request_id": row["request_id"], "order_id": row["order_id"], "customer_id": row["customer_id"], "order_item_id": row["order_item_id"], "approved_quantity": row["quantity"], "approved_amount": float(row["refundable_amount"])}
                cursor.execute("INSERT INTO action_tasks (action_id,request_id,decision_id,action_type,idempotency_key,payload) VALUES (%s,%s,%s,'RefundPayment',%s,%s)", (action_id,row["request_id"],decision_id,f"return-order:{return_order_id}:RefundPayment:v1",Json(payload)))
                cursor.execute("INSERT INTO outbox_events (event_id,aggregate_type,aggregate_id,event_type,payload) VALUES (%s,'ActionTask',%s,'ActionTaskCreated',%s)", (f"EVT-{uuid4().hex}",action_id,Json({"action_id":action_id,"action_type":"RefundPayment",**payload})))
                return_status, request_status = "InspectionAccepted", "ExecutionPending"
                required = ["RefundPayment"]
            else:
                return_status, request_status, required = "InspectionRejected", "Closed", []
            cursor.execute("UPDATE return_orders SET workflow_status=%s, inspection_result=%s, inspection_notes=%s, inspected_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP WHERE return_order_id=%s RETURNING *", (return_status,"Accepted" if accepted else "Rejected",notes,return_order_id))
            result = cursor.fetchone()
            cursor.execute("UPDATE service_requests SET workflow_status=%s, required_actions=%s, resolution_notes=COALESCE(%s,resolution_notes), updated_at=CURRENT_TIMESTAMP WHERE request_id=%s", (request_status,Json(required),notes,row["request_id"]))
            return dict(result)


def _transition(return_order_id: str, current: str, target: str, timestamp_column: str) -> dict[str, Any] | None:
    database_url = get_database_url()
    if not database_url:
        raise RuntimeError("退货流程需要配置 DATABASE_URL")
    import psycopg
    from psycopg.rows import dict_row
    if timestamp_column not in {"received_at"}:
        raise ValueError("非法时间字段")
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(f"UPDATE return_orders SET workflow_status=%s, {timestamp_column}=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP WHERE return_order_id=%s AND workflow_status=%s RETURNING *", (target, return_order_id, current))
            row = cursor.fetchone()
            if row:
                cursor.execute("UPDATE service_requests SET workflow_status='ReturnAwaitingInspection', updated_at=CURRENT_TIMESTAMP WHERE request_id=%s", (row["request_id"],))
            return dict(row) if row else None
