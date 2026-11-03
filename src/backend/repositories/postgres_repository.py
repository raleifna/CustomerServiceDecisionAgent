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
        CREATE SEQUENCE IF NOT EXISTS service_request_item_sequence START 1001
        """,
        """
        CREATE TABLE IF NOT EXISTS order_items (
            order_item_id TEXT PRIMARY KEY,
            order_id TEXT NOT NULL,
            product_id TEXT NOT NULL,
            sku_id TEXT NOT NULL,
            sku_name TEXT NOT NULL,
            unit_price NUMERIC(12, 2) NOT NULL CHECK (unit_price >= 0),
            quantity INTEGER NOT NULL CHECK (quantity > 0),
            discount_amount NUMERIC(12, 2) NOT NULL DEFAULT 0,
            paid_amount NUMERIC(12, 2) NOT NULL CHECK (paid_amount >= 0),
            shipped_product_id TEXT,
            shipped_quantity INTEGER,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_order_items_order_id
        ON order_items (order_id)
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
        CREATE TABLE IF NOT EXISTS human_handoffs (
            handoff_id TEXT PRIMARY KEY,
            customer_id TEXT NOT NULL,
            order_id TEXT,
            request_id TEXT,
            reason TEXT NOT NULL,
            workflow_status TEXT NOT NULL DEFAULT 'Waiting',
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
            target_order_item_id TEXT,
            order_item_count INTEGER NOT NULL DEFAULT 1 CHECK (order_item_count > 0),
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
        "DROP INDEX IF EXISTS uq_open_service_request",
        """
        ALTER TABLE service_requests
            ADD COLUMN IF NOT EXISTS target_order_item_id TEXT,
            ADD COLUMN IF NOT EXISTS order_item_count INTEGER NOT NULL DEFAULT 1
        """,
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_open_service_request_item
        ON service_requests (order_id, target_order_item_id, request_type, request_reason)
        WHERE workflow_status <> 'Closed'
        """,
        """
        CREATE TABLE IF NOT EXISTS service_request_items (
            request_item_id TEXT PRIMARY KEY,
            request_id TEXT NOT NULL REFERENCES service_requests(request_id),
            order_item_id TEXT NOT NULL REFERENCES order_items(order_item_id),
            requested_quantity INTEGER NOT NULL CHECK (requested_quantity > 0),
            approved_quantity INTEGER,
            approved_amount NUMERIC(12, 2),
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (request_id, order_item_id)
        )
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
        CREATE TABLE IF NOT EXISTS return_orders (
            return_order_id TEXT PRIMARY KEY,
            request_id TEXT NOT NULL UNIQUE REFERENCES service_requests(request_id),
            order_id TEXT NOT NULL,
            customer_id TEXT NOT NULL,
            order_item_id TEXT NOT NULL REFERENCES order_items(order_item_id),
            quantity INTEGER NOT NULL CHECK (quantity > 0),
            refundable_amount NUMERIC(12, 2) NOT NULL CHECK (refundable_amount >= 0),
            workflow_status TEXT NOT NULL DEFAULT 'AwaitingShipment',
            return_carrier TEXT,
            return_tracking_number TEXT,
            shipped_at TIMESTAMPTZ,
            received_at TIMESTAMPTZ,
            inspected_at TIMESTAMPTZ,
            inspection_result TEXT,
            inspection_notes TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_return_orders_status ON return_orders (workflow_status, created_at)",
        # Shadow rule-engine suggestion for escalated / non-automatic requests
        # (decision/engine.py shadow_recommendation), compared with the human
        # outcome by services/ai_review_service.py.
        "ALTER TABLE service_requests ADD COLUMN IF NOT EXISTS ai_recommendation JSONB",
        # Live human chat on top of human_handoffs (live_chat_repository.py).
        """
        ALTER TABLE human_handoffs
            ADD COLUMN IF NOT EXISTS staff_id TEXT,
            ADD COLUMN IF NOT EXISTS conversation_id TEXT,
            ADD COLUMN IF NOT EXISTS accepted_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS ended_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS end_reason TEXT,
            ADD COLUMN IF NOT EXISTS last_activity_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        """,
        "CREATE INDEX IF NOT EXISTS ix_human_handoffs_open ON human_handoffs (customer_id, workflow_status)",
        "CREATE INDEX IF NOT EXISTS ix_human_handoffs_updated ON human_handoffs (updated_at)",
        """
        CREATE TABLE IF NOT EXISTS live_messages (
            message_id BIGSERIAL PRIMARY KEY,
            handoff_id TEXT NOT NULL REFERENCES human_handoffs(handoff_id),
            sender TEXT NOT NULL CHECK (sender IN ('customer', 'staff', 'system')),
            sender_id TEXT,
            content TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_live_messages_handoff ON live_messages (handoff_id, message_id)",
        """
        CREATE INDEX IF NOT EXISTS ix_evidence_request_id
        ON service_request_evidence (request_id, created_at)
        """,
        # Finalization results and human-review audit trail. Added via ALTER
        # so existing databases created before this migration pick them up too.
        """
        ALTER TABLE service_requests
            ADD COLUMN IF NOT EXISTS reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
            ADD COLUMN IF NOT EXISTS rule_hits JSONB NOT NULL DEFAULT '[]'::jsonb,
            ADD COLUMN IF NOT EXISTS required_actions JSONB NOT NULL DEFAULT '[]'::jsonb,
            ADD COLUMN IF NOT EXISTS resolved_by TEXT,
            ADD COLUMN IF NOT EXISTS resolution_notes TEXT,
            ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS assigned_to TEXT,
            ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS row_version INTEGER NOT NULL DEFAULT 0
        """,
        """
        CREATE TABLE IF NOT EXISTS audit_logs (
            audit_id TEXT PRIMARY KEY,
            actor_id TEXT NOT NULL,
            actor_role TEXT NOT NULL,
            action TEXT NOT NULL,
            resource_type TEXT NOT NULL,
            resource_id TEXT NOT NULL,
            outcome TEXT NOT NULL,
            details JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_audit_resource
        ON audit_logs (resource_type, resource_id, created_at)
        """,
        """
        ALTER TABLE service_request_evidence
            ADD COLUMN IF NOT EXISTS reviewed_by TEXT,
            ADD COLUMN IF NOT EXISTS review_notes TEXT,
            ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS customer_description TEXT
        """,
        """
        CREATE TABLE IF NOT EXISTS decision_records (
            decision_id TEXT PRIMARY KEY,
            request_id TEXT NOT NULL,
            decision_source TEXT NOT NULL,
            decision TEXT,
            approved_quantity INTEGER,
            approved_amount NUMERIC(12, 2),
            decision_confidence DOUBLE PRECISION,
            matched_rule_id TEXT,
            business_rules_version TEXT NOT NULL,
            decision_rules_version TEXT NOT NULL,
            input_snapshot_hash TEXT NOT NULL,
            reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
            rule_hits JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_decision_request_snapshot
        ON decision_records (request_id, input_snapshot_hash, decision_source)
        """,
        """
        CREATE TABLE IF NOT EXISTS action_tasks (
            action_id TEXT PRIMARY KEY,
            request_id TEXT NOT NULL,
            decision_id TEXT NOT NULL,
            action_type TEXT NOT NULL,
            idempotency_key TEXT NOT NULL UNIQUE,
            workflow_status TEXT NOT NULL DEFAULT 'Pending',
            payload JSONB NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 5,
            next_attempt_at TIMESTAMPTZ,
            last_error TEXT,
            external_transaction_id TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_action_tasks_dispatch
        ON action_tasks (workflow_status, next_attempt_at, created_at)
        """,
        """
        CREATE TABLE IF NOT EXISTS outbox_events (
            event_id TEXT PRIMARY KEY,
            aggregate_type TEXT NOT NULL,
            aggregate_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            payload JSONB NOT NULL,
            publish_status TEXT NOT NULL DEFAULT 'Pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            published_at TIMESTAMPTZ,
            last_error TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_outbox_pending
        ON outbox_events (publish_status, created_at)
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


def create_postgres_fulfillment_request(
    database_url: str,
    order_id: str,
    customer_id: str,
    request_type: str,
    created_date: date,
) -> tuple[dict[str, Any], bool]:
    """Atomically create one open fulfillment-side request per (order, type).

    Generic across request_type values (ExpediteShipment, LogisticsChase, ...)
    — they all share the same table/uniqueness rule (one open request per
    order per type), just tagged differently.
    """
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
            {request_id_sql}, %s, %s, %s, %s, 'Submitted', NULL
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
          AND request_type = %s
          AND workflow_status IN ('Submitted', 'InProgress')
        ORDER BY created_at DESC
        LIMIT 1
    """

    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                insert_sql,
                (created_date, order_id, customer_id, request_type, created_date),
            )
            record = cursor.fetchone()
            created = record is not None

            if record is None:
                cursor.execute(find_sql, (order_id, request_type))
                record = cursor.fetchone()

            if record is None:
                raise RuntimeError("履约请求未创建，且未找到已有请求")

    normalized = dict(record)
    normalized["created_date"] = normalized["created_date"].isoformat()
    return normalized, created
