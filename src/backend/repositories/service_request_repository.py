from datetime import date
from typing import Any

from src.backend.repositories.mock_db_repository import (
    get_service_request as get_seed_service_request,
    get_service_requests_by_order_id as get_seed_requests_by_order_id,
    load_table,
)
from src.backend.repositories.postgres_repository import get_database_url


SERVICE_REQUEST_FIELDS = """
    request_id, order_id, customer_id, request_type, request_reason,
    target_order_item_id, order_item_count,
    requested_quantity, request_date, workflow_status, decision,
    approved_quantity, approved_amount, decision_confidence,
    reasons, rule_hits, required_actions,
    resolved_by, resolution_notes, resolved_at,
    assigned_to, lease_expires_at, row_version,
    ai_recommendation
"""


def _normalize(record: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(record)
    if isinstance(normalized.get("request_date"), date):
        normalized["request_date"] = normalized["request_date"].isoformat()
    if normalized.get("approved_amount") is not None:
        normalized["approved_amount"] = float(normalized["approved_amount"])
    resolved_at = normalized.get("resolved_at")
    if resolved_at is not None and not isinstance(resolved_at, str):
        normalized["resolved_at"] = resolved_at.isoformat()
    lease_expires_at = normalized.get("lease_expires_at")
    if lease_expires_at is not None and not isinstance(lease_expires_at, str):
        normalized["lease_expires_at"] = lease_expires_at.isoformat()
    return normalized


def get_service_request(request_id: str) -> dict[str, Any] | None:
    """Look up by id across the two-tier service-request store.

    Not a "DB read failed, fall back to fixture" degrade -- the two id
    spaces are disjoint by construction (SR\\d{10} fixture ids vs
    SRL\\d{10} live ids, see create_service_request()'s own comment and
    REQUEST_ID_PATTERN), so a Postgres miss here always means "this id is
    a fixture id", never "the real record went missing". Falling through
    to the seed lookup is the only way to read one of the 27 fixture
    requests at all, since they are intentionally never written to
    Postgres.
    """
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
    """Union, not fallback: a fixture order's pre-scripted demo requests
    (JSON) plus any real requests a live conversation has since created
    against that same order_id (Postgres). Both are genuine records that
    can coexist for the same order -- one order_id, two independent
    sources of true rows -- unlike get_service_request()'s single-id
    lookup, where a Postgres miss unambiguously means "this is a fixture
    id" rather than "there might also be a live one to merge in"."""
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


def get_service_requests_by_customer_id(customer_id: str) -> list[dict[str, Any]]:
    """Return the complete customer-visible case list, newest first."""
    database_url = get_database_url()
    if not database_url:
        return [
            record
            for record in load_table("service_requests.json")
            if record.get("customer_id") == customer_id
        ]

    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT {SERVICE_REQUEST_FIELDS} FROM service_requests "
                "WHERE customer_id = %s ORDER BY created_at DESC",
                (customer_id,),
            )
            return [_normalize(dict(record)) for record in cursor.fetchall()]


def get_recent_submission_stats(
    order_id: str,
    customer_id: str,
    *,
    window_days: int = 30,
) -> dict[str, Any]:
    """Return live submission volume and whether the latest case was withdrawn."""
    database_url = get_database_url()
    if not database_url:
        return {"submission_count": 0, "latest_was_withdrawn": False}

    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    count(*) FILTER (
                        WHERE created_at >= CURRENT_TIMESTAMP - (%s * INTERVAL '1 day')
                    ) AS submission_count,
                    (
                        SELECT resolution_notes = '客户主动撤销售后申请'
                        FROM service_requests latest
                        WHERE latest.order_id = %s AND latest.customer_id = %s
                        ORDER BY latest.created_at DESC
                        LIMIT 1
                    ) AS latest_was_withdrawn
                FROM service_requests
                WHERE order_id = %s AND customer_id = %s
                """,
                (window_days, order_id, customer_id, order_id, customer_id),
            )
            row = cursor.fetchone() or {}
    return {
        "submission_count": int(row.get("submission_count") or 0),
        "latest_was_withdrawn": bool(row.get("latest_was_withdrawn")),
    }


def get_completed_refund_amount(order_id: str) -> float:
    """Sum successfully executed refund amounts for this order.

    The order's own record only tracks shipment/delivery facts — it never
    changes when a refund completes, so this is the only way to show "this
    delivered order has since been refunded" without conflating the two.
    """
    database_url = get_database_url()
    if not database_url:
        return 0.0

    import psycopg

    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT COALESCE(SUM((action.payload->>'approved_amount')::numeric), 0)::float
                FROM action_tasks action
                JOIN service_requests request ON request.request_id = action.request_id
                WHERE request.order_id = %s
                  AND action.action_type = 'RefundPayment'
                  AND action.workflow_status = 'Succeeded'
                """,
                (order_id,),
            )
            row = cursor.fetchone()
    return float(row[0]) if row else 0.0


def get_completed_refund_count(customer_id: str) -> int:
    """Count this customer's approved refunds/partial-refunds, live.

    customers.refund_history_count in the mock seed data is a fixed snapshot
    that nothing in this codebase increments — a customer who racks up
    refunds during the system's own operation would never trip the
    high-frequency-refund risk check (RISK-001). Computing it from
    service_requests instead keeps it accurate without needing a writable
    counter column/table.
    """
    database_url = get_database_url()
    if not database_url:
        return 0

    import psycopg

    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT COUNT(*)
                FROM service_requests
                WHERE customer_id = %s
                  AND decision IN ('Approve', 'PartialApprove')
                  AND request_type IN ('Refund', 'PartialRefund')
                """,
                (customer_id,),
            )
            row = cursor.fetchone()
    return int(row[0]) if row else 0


def get_already_approved_quantity(
    order_item_id: str,
    request_reason: str,
    *,
    exclude_request_id: str | None = None,
) -> int:
    """Sum quantity already paid out for this exact item+reason.

    Prevents re-approving the same shortfall/defect after the request that
    paid it out has closed — the (order_id, target_order_item_id,
    request_type, request_reason) uniqueness index only blocks concurrent
    *open* duplicates, not a fresh request created after the prior one
    closed. Decision rules must clamp approved_quantity against
    (ordered_quantity - this) instead of the raw ordered/shipped delta.
    """
    database_url = get_database_url()
    if not database_url:
        return 0

    import psycopg

    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT COALESCE(SUM(approved_quantity), 0)
                FROM service_requests
                WHERE target_order_item_id = %s
                  AND request_reason = %s
                  AND decision IN ('Approve', 'PartialApprove')
                  AND request_id != COALESCE(%s, '')
                """,
                (order_item_id, request_reason, exclude_request_id),
            )
            row = cursor.fetchone()
    return int(row[0]) if row else 0


def get_prior_rejected_request_count(
    order_item_id: str,
    request_reason: str,
    *,
    exclude_request_id: str | None = None,
) -> int:
    """Count closed, rejected-for-insufficient-evidence requests for this item+reason.

    Used to cap self-service resubmission for evidence-gated reasons
    (质量问题/商品损坏): after this many prior Reject closures, a new
    request for the same item+reason must go to human review instead of
    running the automated flow again.
    """
    database_url = get_database_url()
    if not database_url:
        return 0

    import psycopg

    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT COUNT(*)
                FROM service_requests
                WHERE target_order_item_id = %s
                  AND request_reason = %s
                  AND decision = 'Reject'
                  AND request_id != COALESCE(%s, '')
                """,
                (order_item_id, request_reason, exclude_request_id),
            )
            row = cursor.fetchone()
    return int(row[0]) if row else 0


def withdraw_service_request(request_id: str, customer_id: str) -> dict[str, Any] | None:
    """Withdraw an unresolved request before any fulfillment task starts."""
    database_url = get_database_url()
    if not database_url:
        raise RuntimeError("撤销售后申请需要配置 DATABASE_URL")
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""UPDATE service_requests AS request
                    SET workflow_status = 'Closed', decision = 'NoActionRequired',
                        resolution_notes = '客户主动撤销售后申请',
                        resolved_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP,
                        assigned_to = NULL, lease_expires_at = NULL,
                        row_version = row_version + 1
                    WHERE request_id = %s AND customer_id = %s
                      AND decision IS NULL
                      AND workflow_status IN ('Pending', 'NeedsReflection', 'EscalatedToHuman', 'InReview')
                      AND NOT EXISTS (
                          SELECT 1 FROM action_tasks action
                          WHERE action.request_id = request.request_id
                            AND action.workflow_status IN ('Pending','Executing','Retrying','Succeeded')
                      )
                    RETURNING {SERVICE_REQUEST_FIELDS}""",
                (request_id, customer_id),
            )
            record = cursor.fetchone()
            return _normalize(dict(record)) if record else None


def create_service_request(
    *,
    order_id: str,
    customer_id: str,
    request_type: str,
    request_reason: str,
    requested_quantity: int,
    order_item: dict[str, Any],
    order_item_count: int = 1,
    request_date: date | None = None,
) -> tuple[dict[str, Any], bool]:
    """Create a new request, or return the existing open one for this
    (order, type, reason) if there already is one.

    Only checks live Postgres rows — deliberately NOT the JSON seed data.
    Seed records are a fixed case library for demos/eval (docs/04_Architecture.md
    "路径 A/B/C/D"), not open tickets; treating them as open tickets meant a
    live conversation could get silently absorbed into a demo record instead
    of creating a real one, purely because it happened to share the same
    order/type/reason. Postgres's own unique index below is what actually
    prevents duplicate *live* submissions.
    """
    database_url = get_database_url()
    if not database_url:
        raise RuntimeError("创建售后申请需要配置 DATABASE_URL")

    import psycopg
    from psycopg.rows import dict_row

    submitted_date = request_date or date.today()
    order_item_id = order_item.get("order_item_id")
    if not order_item_id or order_item.get("order_id") != order_id:
        raise ValueError("订单项不存在或不属于当前订单")
    if requested_quantity > order_item.get("quantity", 0):
        raise ValueError("申请数量不能超过订单项购买数量")
    insert_sql = f"""
        INSERT INTO service_requests (
            request_id, order_id, customer_id, request_type, request_reason,
            target_order_item_id, order_item_count, requested_quantity,
            request_date, workflow_status
        )
        VALUES (
            -- 'SRL' (Service Request, Live), not 'SR' -- the 27 fixture
            -- service requests (data/mock_db/service_requests.json) are a
            -- fixed demo case library that is NEVER written to Postgres
            -- (see this function's own docstring), hand-authored as
            -- 'SR' + YYYYMM + 4 digits. A live request generated with that
            -- same 'SR' prefix and the same YYYYMM+4-digit shape could, in
            -- principle, collide with one of those 27 ids once
            -- service_request_sequence climbed high enough -- not a
            -- silently-masked failure, but a genuine ambiguity (two
            -- different real records sharing one id). The 'SRL' prefix
            -- makes the two id spaces structurally disjoint instead of
            -- relying on the sequence not having reached that number yet.
            'SRL' || to_char(%s::date, 'YYYYMM') ||
            lpad(nextval('service_request_sequence')::text, 4, '0'),
            %s, %s, %s, %s, %s, %s, %s, %s, 'Pending'
        )
        ON CONFLICT (order_id, target_order_item_id, request_type, request_reason)
        WHERE workflow_status <> 'Closed'
        DO NOTHING
        RETURNING {SERVICE_REQUEST_FIELDS}
    """
    find_sql = f"""
        SELECT {SERVICE_REQUEST_FIELDS}
        FROM service_requests
        WHERE order_id = %s AND target_order_item_id = %s
          AND request_type = %s AND request_reason = %s
          AND workflow_status <> 'Closed'
        ORDER BY created_at DESC LIMIT 1
    """

    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO order_items (
                    order_item_id, order_id, product_id, sku_id, sku_name,
                    unit_price, quantity, discount_amount, paid_amount,
                    shipped_product_id, shipped_quantity
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (order_item_id) DO NOTHING
                """,
                (
                    order_item_id, order_id, order_item["product_id"],
                    order_item["sku_id"], order_item["sku_name"],
                    order_item["unit_price"], order_item["quantity"],
                    order_item.get("discount_amount", 0), order_item["paid_amount"],
                    order_item.get("shipped_product_id"),
                    order_item.get("shipped_quantity"),
                ),
            )
            cursor.execute(
                insert_sql,
                (
                    submitted_date,
                    order_id,
                    customer_id,
                    request_type,
                    request_reason,
                    order_item_id,
                    order_item_count,
                    requested_quantity,
                    submitted_date,
                ),
            )
            record = cursor.fetchone()
            created = record is not None
            if record is None:
                cursor.execute(
                    find_sql, (order_id, order_item_id, request_type, request_reason)
                )
                record = cursor.fetchone()
            if record is None:
                raise RuntimeError("售后申请未创建，且未找到已有申请")
            cursor.execute(
                """
                INSERT INTO service_request_items (
                    request_item_id, request_id, order_item_id, requested_quantity
                ) VALUES (
                    'SRI' || to_char(%s::date, 'YYYYMM') ||
                    lpad(nextval('service_request_item_sequence')::text, 4, '0'),
                    %s, %s, %s
                )
                ON CONFLICT (request_id, order_item_id) DO NOTHING
                """,
                (submitted_date, record["request_id"], order_item_id, requested_quantity),
            )

    return _normalize(dict(record)), created


def update_service_request_result(
    request_id: str,
    *,
    workflow_status: str,
    decision: str | None,
    approved_quantity: int | None,
    approved_amount: float | None,
    decision_confidence: float | None,
    reasons: list[str],
    rule_hits: list[str],
    required_actions: list[str],
    ai_recommendation: dict[str, Any] | None = None,
) -> None:
    """Persist Finalization's outcome. No-ops without a database (local/eval).

    ai_recommendation is the shadow rule-engine suggestion for requests that
    did not get an automatic decision; it is overwritten on every run so it
    always reflects the latest evidence."""
    database_url = get_database_url()
    if not database_url:
        return

    import psycopg
    from psycopg.types.json import Json

    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE service_requests
                SET workflow_status = %s,
                    decision = %s,
                    approved_quantity = %s,
                    approved_amount = %s,
                    decision_confidence = %s,
                    reasons = %s,
                    rule_hits = %s,
                    required_actions = %s,
                    ai_recommendation = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE request_id = %s
                """,
                (
                    workflow_status,
                    decision,
                    approved_quantity,
                    approved_amount,
                    decision_confidence,
                    Json(reasons),
                    Json(rule_hits),
                    Json(required_actions),
                    Json(ai_recommendation) if ai_recommendation is not None else None,
                    request_id,
                ),
            )


def list_staff_service_request_queue() -> list[dict[str, Any]]:
    """List service requests currently waiting on a human (escalated or reflecting)."""
    database_url = get_database_url()
    if not database_url:
        return []

    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT {SERVICE_REQUEST_FIELDS}
                FROM service_requests
                WHERE workflow_status IN ('EscalatedToHuman', 'NeedsReflection')
                ORDER BY updated_at
                """
            )
            return [_normalize(dict(record)) for record in cursor.fetchall()]


def list_staff_service_requests(
    *,
    workflow_status: str | None = None,
    query: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Every live service request for the staff case history, newest first.

    Optionally narrowed to one workflow_status and/or a case-insensitive
    substring of request_id / order_id / customer_id. Without a database
    there is no live history -- fall back to the JSON seed requests so the
    page still renders in local fixture mode.
    """
    needle = (query or "").strip().lower()
    database_url = get_database_url()
    if not database_url:
        records = [
            record for record in load_table("service_requests.json")
            if (not workflow_status or record.get("workflow_status") == workflow_status)
            and (not needle or any(
                needle in str(record.get(key, "")).lower()
                for key in ("request_id", "order_id", "customer_id")
            ))
        ]
        return sorted(records, key=lambda record: record.get("request_date", ""), reverse=True)[:limit]

    import psycopg
    from psycopg.rows import dict_row

    conditions, params = [], []
    if workflow_status:
        conditions.append("workflow_status = %s")
        params.append(workflow_status)
    if needle:
        conditions.append(
            "(lower(request_id) LIKE %s OR lower(order_id) LIKE %s OR lower(customer_id) LIKE %s)"
        )
        params.extend([f"%{needle}%"] * 3)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT {SERVICE_REQUEST_FIELDS}, created_at, updated_at
                FROM service_requests {where}
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (*params, limit),
            )
            records = []
            for record in cursor.fetchall():
                item = _normalize(dict(record))
                for key in ("created_at", "updated_at"):
                    if item.get(key) is not None:
                        item[key] = item[key].isoformat()
                records.append(item)
            return records


def claim_service_request(
    request_id: str, *, staff_id: str, lease_minutes: int = 15
) -> dict[str, Any] | None:
    """Claim or renew a human-review lease without stealing active work."""
    database_url = get_database_url()
    if not database_url:
        raise RuntimeError("领取人工工单需要配置 DATABASE_URL")
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                UPDATE service_requests
                SET assigned_to = %s,
                    lease_expires_at = CURRENT_TIMESTAMP + make_interval(mins => %s),
                    row_version = row_version + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE request_id = %s
                  AND workflow_status IN ('EscalatedToHuman', 'NeedsReflection')
                  AND (assigned_to IS NULL OR assigned_to = %s
                       OR lease_expires_at <= CURRENT_TIMESTAMP)
                RETURNING {SERVICE_REQUEST_FIELDS}
                """,
                (staff_id, lease_minutes, request_id, staff_id),
            )
            record = cursor.fetchone()
    return _normalize(dict(record)) if record else None
