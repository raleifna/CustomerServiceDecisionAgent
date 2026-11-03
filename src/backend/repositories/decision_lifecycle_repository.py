import hashlib
import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from src.backend.config.business_rules import load_business_rules
from src.backend.decision.rule_engine import decision_rules_version
from src.backend.repositories.postgres_repository import get_database_url


FINANCIAL_REQUEST_TYPES = {"Refund", "PartialRefund"}


def _stable_hash(value: dict[str, Any]) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _action_type(final_result: dict[str, Any]) -> str | None:
    if final_result.get("decision") not in {"Approve", "PartialApprove"}:
        return None
    if final_result.get("request_reason") == "未发货取消订单":
        return (
            "CancelOrderItem"
            if final_result.get("order_item_count", 1) > 1
            else "CancelOrder"
        )
    if final_result.get("request_reason") == "不想要了":
        return None
    request_type = final_result.get("request_type")
    if request_type in FINANCIAL_REQUEST_TYPES:
        return "RefundPayment"
    if request_type == "Exchange":
        return "CreateExchange"
    return None


def requires_return_shipment(final_result: dict[str, Any]) -> bool:
    """A no-reason ("不想要了") refund is only paid once the goods come back:
    approving it opens a return order instead of an immediate refund task
    (the RefundPayment task is created at warehouse inspection, see
    return_order_repository.inspect_return). Applies to automatic and human
    approvals alike."""
    return (
        final_result.get("decision") in {"Approve", "PartialApprove"}
        and final_result.get("request_reason") == "不想要了"
    )


def _insert_return_order(cursor, final_result: dict[str, Any]) -> None:
    cursor.execute(
        """INSERT INTO return_orders (
               return_order_id, request_id, order_id, customer_id,
               order_item_id, quantity, refundable_amount, workflow_status
           ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'AwaitingShipment')
           ON CONFLICT (request_id) DO NOTHING""",
        (f"RET-{uuid4().hex}", final_result["request_id"],
         final_result.get("order_id"), final_result.get("customer_id"),
         final_result.get("order_item_id"), final_result.get("approved_quantity"),
         final_result.get("approved_amount")),
    )


def build_execution_plan(final_result: dict[str, Any]) -> dict[str, Any] | None:
    action_type = _action_type(final_result)
    if action_type is None:
        return None
    request_id = final_result["request_id"]
    return {
        "action_type": action_type,
        "idempotency_key": f"service-request:{request_id}:{action_type}:v1",
        "payload": {
            "request_id": request_id,
            "order_id": final_result.get("order_id"),
            "customer_id": final_result.get("customer_id"),
            "order_item_id": final_result.get("order_item_id"),
            "approved_quantity": final_result.get("approved_quantity"),
            "approved_amount": final_result.get("approved_amount"),
        },
    }


def persist_decision_lifecycle(
    final_result: dict[str, Any],
    *,
    input_snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Atomically persist decision, execution task and its outbox event.

    Without DATABASE_URL this returns the same planned objects for local demos
    and deterministic evaluation, but deliberately performs no fake execution.
    """
    decision_id = f"DEC-{uuid4().hex}"
    snapshot_hash = _stable_hash(input_snapshot)
    return_eligibility = requires_return_shipment(final_result)
    execution_plan = build_execution_plan(final_result)
    action_id = f"ACT-{uuid4().hex}" if execution_plan else None
    event_id = f"EVT-{uuid4().hex}" if execution_plan else None
    workflow_status = (
        "ReturnAwaitingShipment" if return_eligibility
        else "ExecutionPending" if execution_plan else "Closed"
    )
    lifecycle = {
        "decision_id": decision_id,
        "input_snapshot_hash": snapshot_hash,
        "workflow_status": workflow_status,
        "execution": (
            {
                "action_id": action_id,
                "action_type": execution_plan["action_type"],
                "workflow_status": "Pending",
                "idempotency_key": execution_plan["idempotency_key"],
            }
            if execution_plan
            else None
        ),
    }
    database_url = get_database_url()
    if not database_url:
        return lifecycle

    import psycopg
    from psycopg.rows import dict_row
    from psycopg.types.json import Json

    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT request_id FROM service_requests WHERE request_id = %s FOR UPDATE",
                (final_result["request_id"],),
            )
            if cursor.fetchone() is None:
                raise RuntimeError("售后申请不存在，裁决事务已回滚")
            cursor.execute(
                """
                INSERT INTO decision_records (
                    decision_id, request_id, decision_source, decision,
                    approved_quantity, approved_amount, decision_confidence,
                    matched_rule_id, business_rules_version,
                    decision_rules_version, input_snapshot_hash, reasons, rule_hits
                ) VALUES (%s, %s, 'AutoRule', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (request_id, input_snapshot_hash, decision_source)
                DO NOTHING
                RETURNING decision_id
                """,
                (
                    decision_id,
                    final_result["request_id"],
                    final_result.get("decision"),
                    final_result.get("approved_quantity"),
                    final_result.get("approved_amount"),
                    final_result.get("decision_confidence"),
                    final_result.get("matched_rule_id"),
                    str(load_business_rules().get("version", "unknown")),
                    decision_rules_version(),
                    snapshot_hash,
                    Json(final_result.get("reasons", [])),
                    Json(final_result.get("rule_hits", [])),
                ),
            )
            inserted = cursor.fetchone()
            if inserted is None:
                cursor.execute(
                    """SELECT decision_id FROM decision_records
                       WHERE request_id = %s AND input_snapshot_hash = %s
                         AND decision_source = 'AutoRule'""",
                    (final_result["request_id"], snapshot_hash),
                )
                inserted = cursor.fetchone()
            decision_id = inserted["decision_id"]
            lifecycle["decision_id"] = decision_id

            if execution_plan:
                cursor.execute(
                    """
                    INSERT INTO action_tasks (
                        action_id, request_id, decision_id, action_type,
                        idempotency_key, payload
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (idempotency_key) DO NOTHING
                    RETURNING action_id
                    """,
                    (
                        action_id,
                        final_result["request_id"],
                        decision_id,
                        execution_plan["action_type"],
                        execution_plan["idempotency_key"],
                        Json(execution_plan["payload"]),
                    ),
                )
                action_record = cursor.fetchone()
                created = action_record is not None
                if not created:
                    cursor.execute(
                        "SELECT action_id FROM action_tasks WHERE idempotency_key = %s",
                        (execution_plan["idempotency_key"],),
                    )
                    action_record = cursor.fetchone()
                action_id = action_record["action_id"]
                lifecycle["execution"]["action_id"] = action_id

                if created:
                    event_payload = {
                        "action_id": action_id,
                        "action_type": execution_plan["action_type"],
                        **execution_plan["payload"],
                        "occurred_at": datetime.now(timezone.utc).isoformat(),
                    }
                    cursor.execute(
                        """INSERT INTO outbox_events (
                               event_id, aggregate_type, aggregate_id,
                               event_type, payload
                           ) VALUES (%s, 'ActionTask', %s, 'ActionTaskCreated', %s)""",
                        (event_id, action_id, Json(event_payload)),
                    )

            if return_eligibility:
                _insert_return_order(cursor, final_result)

            cursor.execute(
                """
                UPDATE service_requests
                SET workflow_status = %s, decision = %s,
                    approved_quantity = %s, approved_amount = %s,
                    decision_confidence = %s, reasons = %s, rule_hits = %s,
                    required_actions = %s, updated_at = CURRENT_TIMESTAMP
                WHERE request_id = %s
                """,
                (
                    workflow_status,
                    final_result.get("decision"),
                    final_result.get("approved_quantity"),
                    final_result.get("approved_amount"),
                    final_result.get("decision_confidence"),
                    Json(final_result.get("reasons", [])),
                    Json(final_result.get("rule_hits", [])),
                    Json(
                        [execution_plan["action_type"]] if execution_plan
                        else ["CustomerShipReturn"] if return_eligibility else []
                    ),
                    final_result["request_id"],
                ),
            )
            if final_result.get("order_item_id"):
                cursor.execute(
                    """UPDATE service_request_items
                       SET approved_quantity = %s, approved_amount = %s
                       WHERE request_id = %s AND order_item_id = %s""",
                    (
                        final_result.get("approved_quantity"),
                        final_result.get("approved_amount"),
                        final_result["request_id"],
                        final_result["order_item_id"],
                    ),
                )
    return lifecycle


def claim_next_action() -> dict[str, Any] | None:
    """Atomically lease one due action; safe with multiple worker processes."""
    database_url = get_database_url()
    if not database_url:
        raise RuntimeError("执行履约任务需要配置 DATABASE_URL")
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                WITH candidate AS (
                    SELECT action_id FROM action_tasks
                    WHERE workflow_status IN ('Pending', 'Retrying')
                      AND (next_attempt_at IS NULL OR next_attempt_at <= CURRENT_TIMESTAMP)
                    ORDER BY created_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                UPDATE action_tasks AS task
                SET workflow_status = 'Executing', attempts = attempts + 1,
                    updated_at = CURRENT_TIMESTAMP
                FROM candidate
                WHERE task.action_id = candidate.action_id
                RETURNING task.*
                """
            )
            record = cursor.fetchone()
            return dict(record) if record else None


def complete_action(
    action: dict[str, Any],
    *,
    success: bool,
    external_transaction_id: str | None = None,
    error: str | None = None,
) -> str:
    """Finish or reschedule a leased task and emit the matching outbox event."""
    database_url = get_database_url()
    if not database_url:
        raise RuntimeError("更新履约任务需要配置 DATABASE_URL")
    import psycopg
    from psycopg.types.json import Json

    attempts = int(action.get("attempts", 1))
    max_attempts = int(action.get("max_attempts", 5))
    if success:
        action_status = "Succeeded"
        request_status = "Closed"
        event_type = "ActionTaskSucceeded"
    elif attempts >= max_attempts:
        action_status = "Failed"
        request_status = "ExecutionFailed"
        event_type = "ActionTaskFailed"
    else:
        action_status = "Retrying"
        request_status = "ExecutionPending"
        event_type = "ActionTaskRetryScheduled"

    event_id = f"EVT-{uuid4().hex}"
    event_payload = {
        "action_id": action["action_id"],
        "request_id": action["request_id"],
        "action_type": action["action_type"],
        "workflow_status": action_status,
        "attempts": attempts,
        "external_transaction_id": external_transaction_id,
        "error": error,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
    }
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE action_tasks
                SET workflow_status = %s, external_transaction_id = %s,
                    last_error = %s,
                    next_attempt_at = CASE WHEN %s = 'Retrying'
                        THEN CURRENT_TIMESTAMP + make_interval(secs => LEAST(3600, 30 * power(2, attempts - 1))::int)
                        ELSE NULL END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE action_id = %s AND workflow_status = 'Executing'
                """,
                (action_status, external_transaction_id, error, action_status, action["action_id"]),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("履约任务租约已失效或状态不允许完成")
            cursor.execute(
                """UPDATE service_requests SET workflow_status = %s,
                       updated_at = CURRENT_TIMESTAMP WHERE request_id = %s""",
                (request_status, action["request_id"]),
            )
            cursor.execute(
                """INSERT INTO outbox_events (
                       event_id, aggregate_type, aggregate_id, event_type, payload
                   ) VALUES (%s, 'ActionTask', %s, %s, %s)""",
                (event_id, action["action_id"], event_type, Json(event_payload)),
            )
    return action_status


def resolve_human_decision(
    request_id: str,
    *,
    decision: str,
    approved_quantity: int | None,
    approved_amount: float | None,
    staff_id: str,
    staff_role: str,
    expected_version: int,
    resolution_notes: str | None,
) -> dict[str, Any] | None:
    """Atomically resolve claimed work and create any required execution task."""
    database_url = get_database_url()
    if not database_url:
        raise RuntimeError("人工处理售后申请需要配置 DATABASE_URL")
    import psycopg
    from psycopg.rows import dict_row
    from psycopg.types.json import Json

    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT * FROM service_requests WHERE request_id = %s
                   FOR UPDATE""",
                (request_id,),
            )
            request = cursor.fetchone()
            if request is None:
                return None
            request = dict(request)
            if request["workflow_status"] not in {"EscalatedToHuman", "NeedsReflection"}:
                raise RuntimeError("该申请当前状态不允许人工裁决")
            if request.get("assigned_to") != staff_id:
                raise RuntimeError("请先领取该工单，或工单已由其他客服领取")
            lease = request.get("lease_expires_at")
            if lease is None or lease <= datetime.now(timezone.utc):
                raise RuntimeError("工单领取租约已过期，请重新领取")
            if request.get("row_version") != expected_version:
                raise RuntimeError("工单版本已经变化，请刷新后重新处理")

            cursor.execute(
                "SELECT order_item_id FROM service_request_items WHERE request_id = %s",
                (request_id,),
            )
            request_item_rows = cursor.fetchall()
            if len(request_item_rows) != 1:
                raise RuntimeError("人工裁决要求申请明确关联一个订单项")
            order_item_id = request_item_rows[0]["order_item_id"]

            cursor.execute(
                "SELECT paid_amount FROM order_items WHERE order_item_id = %s",
                (order_item_id,),
            )
            order_item_row = cursor.fetchone()
            if order_item_row is None:
                raise RuntimeError("人工裁决关联的订单项不存在")
            max_approved_amount = float(order_item_row["paid_amount"])

            validate_human_decision(
                request_type=request["request_type"],
                requested_quantity=request["requested_quantity"],
                decision=decision,
                approved_quantity=approved_quantity,
                approved_amount=approved_amount,
                max_approved_amount=max_approved_amount,
            )
            final_result = {
                "request_id": request_id,
                "order_id": request["order_id"],
                "customer_id": request["customer_id"],
                "request_type": request["request_type"],
                "request_reason": request["request_reason"],
                "order_item_id": order_item_id,
                "order_item_count": request.get("order_item_count", 1),
                "decision": decision,
                "approved_quantity": approved_quantity,
                "approved_amount": approved_amount,
            }
            execution_plan = build_execution_plan(final_result)
            return_eligibility = requires_return_shipment(final_result)
            workflow_status = (
                "ReturnAwaitingShipment" if return_eligibility
                else "ExecutionPending" if execution_plan else "Closed"
            )
            snapshot_hash = _stable_hash({
                "request_id": request_id,
                "row_version": expected_version,
                "decision": decision,
                "approved_quantity": approved_quantity,
                "approved_amount": approved_amount,
                "staff_id": staff_id,
            })
            decision_id = f"DEC-{uuid4().hex}"
            cursor.execute(
                """
                INSERT INTO decision_records (
                    decision_id, request_id, decision_source, decision,
                    approved_quantity, approved_amount, decision_confidence,
                    matched_rule_id, business_rules_version,
                    decision_rules_version, input_snapshot_hash, reasons, rule_hits
                ) VALUES (%s, %s, 'Human', %s, %s, %s, NULL, NULL, %s, %s, %s, %s, '[]'::jsonb)
                """,
                (
                    decision_id, request_id, decision, approved_quantity, approved_amount,
                    str(load_business_rules().get("version", "unknown")),
                    decision_rules_version(), snapshot_hash,
                    Json([resolution_notes] if resolution_notes else []),
                ),
            )
            action_id = None
            if execution_plan:
                action_id = f"ACT-{uuid4().hex}"
                cursor.execute(
                    """INSERT INTO action_tasks (
                           action_id, request_id, decision_id, action_type,
                           idempotency_key, payload
                       ) VALUES (%s, %s, %s, %s, %s, %s)
                       ON CONFLICT (idempotency_key) DO NOTHING
                       RETURNING action_id""",
                    (action_id, request_id, decision_id, execution_plan["action_type"],
                     execution_plan["idempotency_key"], Json(execution_plan["payload"])),
                )
                created = cursor.fetchone()
                if created is None:
                    cursor.execute(
                        "SELECT action_id FROM action_tasks WHERE idempotency_key = %s",
                        (execution_plan["idempotency_key"],),
                    )
                    action_id = cursor.fetchone()["action_id"]
                else:
                    cursor.execute(
                        """INSERT INTO outbox_events (
                               event_id, aggregate_type, aggregate_id, event_type, payload
                           ) VALUES (%s, 'ActionTask', %s, 'ActionTaskCreated', %s)""",
                        (f"EVT-{uuid4().hex}", action_id, Json({
                            "action_id": action_id, "request_id": request_id,
                            "action_type": execution_plan["action_type"],
                            **execution_plan["payload"],
                        })),
                    )

            if return_eligibility:
                _insert_return_order(cursor, final_result)

            cursor.execute(
                """
                UPDATE service_requests
                SET workflow_status = %s, decision = %s,
                    approved_quantity = %s, approved_amount = %s,
                    decision_confidence = NULL, resolved_by = %s,
                    resolution_notes = %s, resolved_at = CURRENT_TIMESTAMP,
                    required_actions = %s, assigned_to = NULL,
                    lease_expires_at = NULL, row_version = row_version + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE request_id = %s AND row_version = %s
                RETURNING *
                """,
                (workflow_status, decision, approved_quantity, approved_amount,
                 staff_id, resolution_notes,
                 Json([execution_plan["action_type"]] if execution_plan else []),
                 request_id, expected_version),
            )
            resolved = cursor.fetchone()
            if resolved is None:
                raise RuntimeError("工单版本冲突，人工裁决未提交")
            cursor.execute(
                """UPDATE service_request_items
                   SET approved_quantity = %s, approved_amount = %s
                   WHERE request_id = %s AND order_item_id = %s""",
                (approved_quantity, approved_amount, request_id, order_item_id),
            )
            cursor.execute(
                """INSERT INTO audit_logs (
                       audit_id, actor_id, actor_role, action, resource_type,
                       resource_id, outcome, details
                   ) VALUES (%s, %s, %s, 'ResolveServiceRequest',
                             'ServiceRequest', %s, 'Succeeded', %s)""",
                (f"AUD-{uuid4().hex}", staff_id, staff_role, request_id, Json({
                    "decision_id": decision_id, "decision": decision,
                    "action_id": action_id, "workflow_status": workflow_status,
                })),
            )
            return dict(resolved)


def validate_human_decision(
    *, request_type: str, requested_quantity: int, decision: str,
    approved_quantity: int | None, approved_amount: float | None,
    max_approved_amount: float | None = None,
) -> None:
    if decision in {"Reject", "NoActionRequired"}:
        if approved_quantity is not None or approved_amount is not None:
            raise ValueError("拒绝或无需操作时不能填写批准数量和金额")
        return
    if not isinstance(approved_quantity, int) or not 0 < approved_quantity <= requested_quantity:
        raise ValueError("批准数量必须大于0且不超过申请数量")
    if decision == "PartialApprove" and approved_quantity >= requested_quantity:
        raise ValueError("部分批准的数量必须小于申请数量")
    if request_type in FINANCIAL_REQUEST_TYPES:
        if not isinstance(approved_amount, (int, float)) or approved_amount <= 0:
            raise ValueError("退款类批准必须填写正数批准金额")
        if max_approved_amount is not None and approved_amount > max_approved_amount:
            raise ValueError(f"批准金额不能超过该订单项实付金额 {max_approved_amount}")
    elif approved_amount is not None:
        raise ValueError("换货申请不能填写退款金额")


def list_failed_actions() -> list[dict[str, Any]]:
    database_url = get_database_url()
    if not database_url:
        return []
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT action_id, request_id, action_type, workflow_status,
                          attempts, max_attempts, last_error,
                          external_transaction_id, created_at, updated_at
                   FROM action_tasks WHERE workflow_status = 'Failed'
                   ORDER BY updated_at"""
            )
            return [dict(record) for record in cursor.fetchall()]


def count_actions_by_status() -> dict[str, int]:
    """workflow_status -> number of action tasks, for the staff dashboard."""
    database_url = get_database_url()
    if not database_url:
        return {}
    import psycopg
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT workflow_status, count(*) FROM action_tasks GROUP BY workflow_status")
            return {status: count for status, count in cursor.fetchall()}


def list_actions_for_request(request_id: str) -> list[dict[str, Any]]:
    database_url = get_database_url()
    if not database_url:
        return []
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT action_id, request_id, action_type, workflow_status,
                          attempts, max_attempts, last_error,
                          external_transaction_id, created_at, updated_at
                   FROM action_tasks WHERE request_id = %s ORDER BY created_at""",
                (request_id,),
            )
            records = []
            for record in cursor.fetchall():
                item = dict(record)
                for key in ("created_at", "updated_at"):
                    if item.get(key) is not None:
                        item[key] = item[key].isoformat()
                records.append(item)
            return records


def retry_failed_action(
    action_id: str, *, staff_id: str, staff_role: str
) -> dict[str, Any] | None:
    """Requeue the same idempotent operation; never creates a duplicate action."""
    database_url = get_database_url()
    if not database_url:
        raise RuntimeError("重试履约任务需要配置 DATABASE_URL")
    import psycopg
    from psycopg.rows import dict_row
    from psycopg.types.json import Json
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """UPDATE action_tasks
                   SET workflow_status = 'Retrying', attempts = 0,
                       next_attempt_at = CURRENT_TIMESTAMP, last_error = NULL,
                       updated_at = CURRENT_TIMESTAMP
                   WHERE action_id = %s AND workflow_status = 'Failed'
                   RETURNING *""",
                (action_id,),
            )
            action = cursor.fetchone()
            if action is None:
                return None
            action = dict(action)
            cursor.execute(
                """UPDATE service_requests SET workflow_status = 'ExecutionPending',
                       updated_at = CURRENT_TIMESTAMP WHERE request_id = %s""",
                (action["request_id"],),
            )
            details = {"action_id": action_id, "request_id": action["request_id"]}
            cursor.execute(
                """INSERT INTO outbox_events (
                       event_id, aggregate_type, aggregate_id, event_type, payload
                   ) VALUES (%s, 'ActionTask', %s, 'ActionTaskManuallyRetried', %s)""",
                (f"EVT-{uuid4().hex}", action_id, Json(details)),
            )
            cursor.execute(
                """INSERT INTO audit_logs (
                       audit_id, actor_id, actor_role, action, resource_type,
                       resource_id, outcome, details
                   ) VALUES (%s, %s, %s, 'RetryActionTask',
                             'ActionTask', %s, 'Succeeded', %s)""",
                (f"AUD-{uuid4().hex}", staff_id, staff_role, action_id, Json(details)),
            )
            return action
