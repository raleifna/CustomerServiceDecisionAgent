import json
import os
import uuid
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from src.backend.repositories.decision_lifecycle_repository import (
    claim_next_action,
    complete_action,
)


@dataclass
class ConnectorResult:
    external_transaction_id: str


class ActionConnector(Protocol):
    def execute(self, action_type: str, payload: dict[str, Any], idempotency_key: str) -> ConnectorResult: ...


class HttpActionConnector:
    """HTTP adapter with strict timeout and end-to-end idempotency.

    Falls back to an instant simulated success when no external endpoint is
    configured for an action type, so demo/local environments (no real
    payment gateway or warehouse system) still complete the fulfillment
    lifecycle end-to-end instead of leaving action_tasks stuck Pending.
    """

    ENVIRONMENTS = {
        "RefundPayment": "PAYMENT_REFUND_URL",
        "CreateExchange": "WAREHOUSE_EXCHANGE_URL",
        "CancelOrder": "ORDER_CANCEL_URL",
        "CancelOrderItem": "ORDER_CANCEL_URL",
    }

    def __init__(self, timeout_seconds: float = 5.0):
        self.timeout_seconds = timeout_seconds

    def execute(
        self, action_type: str, payload: dict[str, Any], idempotency_key: str
    ) -> ConnectorResult:
        env_name = self.ENVIRONMENTS.get(action_type)
        endpoint = os.getenv(env_name, "").strip() if env_name else ""
        if not endpoint:
            return ConnectorResult(
                external_transaction_id=f"SIM-{idempotency_key}-{uuid.uuid4().hex[:8]}"
            )
        request = Request(
            endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Idempotency-Key": idempotency_key,
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"外部履约调用失败: {exc}") from exc
        transaction_id = body.get("transaction_id")
        if not isinstance(transaction_id, str) or not transaction_id:
            raise RuntimeError("外部履约响应缺少 transaction_id")
        return ConnectorResult(external_transaction_id=transaction_id)


def process_one_action(connector: ActionConnector | None = None) -> dict[str, Any] | None:
    action = claim_next_action()
    if action is None:
        return None
    active_connector = connector or HttpActionConnector()
    try:
        result = active_connector.execute(
            action["action_type"],
            dict(action["payload"]),
            action["idempotency_key"],
        )
    except Exception as exc:
        status = complete_action(action, success=False, error=str(exc)[:2000])
        return {"action_id": action["action_id"], "status": status, "error": str(exc)}
    status = complete_action(
        action,
        success=True,
        external_transaction_id=result.external_transaction_id,
    )
    return {
        "action_id": action["action_id"],
        "status": status,
        "external_transaction_id": result.external_transaction_id,
    }
