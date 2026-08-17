import os
from datetime import date
from typing import Any


OPEN_WORKFLOW_STATUSES = ("Submitted", "InProgress")


def get_database_url() -> str | None:
    value = os.getenv("DATABASE_URL", "").strip()
    return value or None


def setup_application_schema(database_url: str) -> None:
    """Create the small runtime schema owned by this application."""
    import psycopg

    statements = (
        """
        CREATE SEQUENCE IF NOT EXISTS fulfillment_request_sequence
        """,
        """
        CREATE SEQUENCE IF NOT EXISTS service_request_sequence START 1001
        """,
        """
        CREATE SEQUENCE IF NOT EXISTS evidence_sequence START 1001
        """,
        """
        CREATE TABLE IF NOT EXISTS fulfillment_requests (
            fulfillment_request_id TEXT PRIMARY KEY,
            order_id TEXT NOT NULL,
            customer_id TEXT NOT NULL,
            request_type TEXT NOT NULL,
            created_date DATE NOT NULL,
            workflow_status TEXT NOT NULL,
            result TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_open_fulfillment_request
        ON fulfillment_requests (order_id, request_type)
        WHERE workflow_status IN ('Submitted', 'InProgress')
        """,
        """
        CREATE TABLE IF NOT EXISTS service_requests (
            request_id TEXT PRIMARY KEY,
            order_id TEXT NOT NULL,
            customer_id TEXT NOT NULL,
            request_type TEXT NOT NULL,
            request_reason TEXT NOT NULL,
            requested_quantity INTEGER NOT NULL CHECK (requested_quantity > 0),
            request_date DATE NOT NULL,
            workflow_status TEXT NOT NULL DEFAULT 'Pending',
            decision TEXT,
            approved_quantity INTEGER,
            approved_amount NUMERIC(12, 2),
            decision_confidence DOUBLE PRECISION,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_open_service_request
        ON service_requests (order_id, request_type, request_reason)
        WHERE workflow_status <> 'Closed'
        """,
        """
        CREATE TABLE IF NOT EXISTS service_request_evidence (
            evidence_id TEXT PRIMARY KEY,
            request_id TEXT NOT NULL,
            evidence_type TEXT NOT NULL,
            submitted_by TEXT NOT NULL,
            submitted_date DATE NOT NULL,
            original_filename TEXT NOT NULL,
            media_type TEXT NOT NULL,
            storage_path TEXT NOT NULL UNIQUE,
            file_size_bytes BIGINT NOT NULL CHECK (file_size_bytes > 0),
            verification_status TEXT NOT NULL DEFAULT 'Pending',
            verified_facts JSONB NOT NULL DEFAULT '[]'::jsonb,
            verification_confidence DOUBLE PRECISION,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_evidence_request_id
        ON service_request_evidence (request_id, created_at)
        """,
    )

    with psycopg.connect(database_url, autocommit=True) as connection:
        with connection.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)


def database_is_available(database_url: str | None = None) -> bool:
    target_url = database_url or get_database_url()
    if not target_url:
        return False

    try:
        import psycopg

        with psycopg.connect(target_url, connect_timeout=2) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                return cursor.fetchone() == (1,)
    except Exception:
        return False


def create_postgres_expedite_request(
    database_url: str,
    order_id: str,
    customer_id: str,
    created_date: date,
) -> tuple[dict[str, Any], bool]:
    """Atomically create one open expedite request per order."""
    import psycopg
    from psycopg.rows import dict_row

    request_id_sql = """
        'FR' || to_char(%s::date, 'YYYYMM') ||
        lpad(nextval('fulfillment_request_sequence')::text, 4, '0')
    """
    insert_sql = f"""
        INSERT INTO fulfillment_requests (
            fulfillment_request_id,
            order_id,
            customer_id,
            request_type,
            created_date,
            workflow_status,
            result
        )
        VALUES (
            {request_id_sql}, %s, %s, 'ExpediteShipment', %s, 'Submitted', NULL
        )
        ON CONFLICT (order_id, request_type)
        WHERE workflow_status IN ('Submitted', 'InProgress')
        DO NOTHING
        RETURNING fulfillment_request_id, order_id, customer_id,
                  request_type, created_date, workflow_status, result
    """
    find_sql = """
        SELECT fulfillment_request_id, order_id, customer_id,
               request_type, created_date, workflow_status, result
        FROM fulfillment_requests
        WHERE order_id = %s
          AND request_type = 'ExpediteShipment'
          AND workflow_status IN ('Submitted', 'InProgress')
        ORDER BY created_at DESC
        LIMIT 1
    """

    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                insert_sql,
                (created_date, order_id, customer_id, created_date),
            )
            record = cursor.fetchone()
            created = record is not None

            if record is None:
                cursor.execute(find_sql, (order_id,))
                record = cursor.fetchone()

            if record is None:
                raise RuntimeError("催发货请求未创建，且未找到已有请求")

    normalized = dict(record)
    normalized["created_date"] = normalized["created_date"].isoformat()
    return normalized, created
