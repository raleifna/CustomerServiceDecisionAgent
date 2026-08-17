from datetime import date
from typing import Any

from src.backend.repositories.mock_db_repository import (
    get_service_request as get_seed_service_request,
    get_service_requests_by_order_id as get_seed_requests_by_order_id,
)
from src.backend.repositories.postgres_repository import get_database_url


SERVICE_REQUEST_FIELDS = """
    request_id, order_id, customer_id, request_type, request_reason,
    requested_quantity, request_date, workflow_status, decision,
    approved_quantity, approved_amount, decision_confidence
"""


def _normalize(record: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(record)
    if isinstance(normalized.get("request_date"), date):
        normalized["request_date"] = normalized["request_date"].isoformat()
    if normalized.get("approved_amount") is not None:
        normalized["approved_amount"] = float(normalized["approved_amount"])
    return normalized


def get_service_request(request_id: str) -> dict[str, Any] | None:
    database_url = get_database_url()
    if database_url:
        import psycopg
        from psycopg.rows import dict_row

        with psycopg.connect(database_url, row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT {SERVICE_REQUEST_FIELDS} FROM service_requests WHERE request_id = %s",
                    (request_id,),
                )
                record = cursor.fetchone()
                if record:
                    return _normalize(dict(record))

    return get_seed_service_request(request_id)


def get_service_requests_by_order_id(order_id: str) -> list[dict[str, Any]]:
    records = list(get_seed_requests_by_order_id(order_id))
    database_url = get_database_url()
    if not database_url:
        return records

    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT {SERVICE_REQUEST_FIELDS} FROM service_requests WHERE order_id = %s ORDER BY created_at",
                (order_id,),
            )
            records.extend(_normalize(dict(record)) for record in cursor.fetchall())
    return records


def create_service_request(
    *,
    order_id: str,
    customer_id: str,
    request_type: str,
    request_reason: str,
    requested_quantity: int,
    request_date: date | None = None,
) -> tuple[dict[str, Any], bool]:
    for existing in get_seed_requests_by_order_id(order_id):
        if (
            existing.get("request_type") == request_type
            and existing.get("request_reason") == request_reason
            and existing.get("workflow_status") != "Closed"
        ):
            return existing, False

    database_url = get_database_url()
    if not database_url:
        raise RuntimeError("创建售后申请需要配置 DATABASE_URL")

    import psycopg
    from psycopg.rows import dict_row

    submitted_date = request_date or date.today()
    insert_sql = f"""
        INSERT INTO service_requests (
            request_id, order_id, customer_id, request_type, request_reason,
            requested_quantity, request_date, workflow_status
        )
        VALUES (
            'SR' || to_char(%s::date, 'YYYYMM') ||
            lpad(nextval('service_request_sequence')::text, 4, '0'),
            %s, %s, %s, %s, %s, %s, 'Pending'
        )
        ON CONFLICT (order_id, request_type, request_reason)
        WHERE workflow_status <> 'Closed'
        DO NOTHING
        RETURNING {SERVICE_REQUEST_FIELDS}
    """
    find_sql = f"""
        SELECT {SERVICE_REQUEST_FIELDS}
        FROM service_requests
        WHERE order_id = %s AND request_type = %s AND request_reason = %s
          AND workflow_status <> 'Closed'
        ORDER BY created_at DESC LIMIT 1
    """

    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                insert_sql,
                (
                    submitted_date,
                    order_id,
                    customer_id,
                    request_type,
                    request_reason,
                    requested_quantity,
                    submitted_date,
                ),
            )
            record = cursor.fetchone()
            created = record is not None
            if record is None:
                cursor.execute(find_sql, (order_id, request_type, request_reason))
                record = cursor.fetchone()
            if record is None:
                raise RuntimeError("售后申请未创建，且未找到已有申请")

    return _normalize(dict(record)), created
