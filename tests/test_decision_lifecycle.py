import unittest
from unittest.mock import patch

from src.backend.repositories.decision_lifecycle_repository import build_execution_plan
from src.backend.services.action_execution_service import (
    ConnectorResult,
    process_one_action,
)


class ExecutionPlanTests(unittest.TestCase):
    def test_no_reason_return_waits_for_warehouse_inspection(self) -> None:
        plan = build_execution_plan({
            "request_id": "SR-RETURN", "request_type": "Refund",
            "request_reason": "不想要了", "decision": "Approve",
        })
        self.assertIsNone(plan)

    def test_refund_approval_creates_payment_task(self) -> None:
        plan = build_execution_plan({
            "request_id": "SR1", "order_id": "ORD1", "customer_id": "CUS1",
            "request_type": "Refund", "request_reason": "质量问题",
            "decision": "Approve", "approved_quantity": 1, "approved_amount": 99,
        })
        self.assertEqual(plan["action_type"], "RefundPayment")
        self.assertEqual(plan["idempotency_key"], "service-request:SR1:RefundPayment:v1")

    def test_unshipped_cancellation_has_precedence_over_refund(self) -> None:
        plan = build_execution_plan({
            "request_id": "SR2", "order_id": "ORD2", "customer_id": "CUS2",
            "request_type": "Refund", "request_reason": "未发货取消订单",
            "decision": "Approve", "approved_quantity": 1, "approved_amount": 99,
        })
        self.assertEqual(plan["action_type"], "CancelOrder")

    def test_multi_item_order_cancels_only_selected_item(self) -> None:
        plan = build_execution_plan({
            "request_id": "SR4", "order_id": "ORD4", "customer_id": "CUS4",
            "order_item_id": "OI4-B", "order_item_count": 2,
            "request_type": "Refund", "request_reason": "未发货取消订单",
            "decision": "Approve", "approved_quantity": 1, "approved_amount": 399,
        })
        self.assertEqual(plan["action_type"], "CancelOrderItem")
        self.assertEqual(plan["payload"]["order_item_id"], "OI4-B")

    def test_rejection_does_not_create_side_effect(self) -> None:
        self.assertIsNone(build_execution_plan({"request_id": "SR3", "decision": "Reject"}))


class FakeConnector:
    def execute(self, action_type, payload, idempotency_key):
        return ConnectorResult("external-123")


class ActionWorkerTests(unittest.TestCase):
    @patch("src.backend.services.action_execution_service.complete_action")
    @patch("src.backend.services.action_execution_service.claim_next_action")
    def test_worker_completes_claimed_action(self, claim, complete) -> None:
        claim.return_value = {
            "action_id": "ACT1", "request_id": "SR1", "action_type": "RefundPayment",
            "payload": {"approved_amount": 99}, "idempotency_key": "key-1",
        }
        complete.return_value = "Succeeded"
        result = process_one_action(FakeConnector())
        complete.assert_called_once_with(
            claim.return_value, success=True, external_transaction_id="external-123"
        )
        self.assertEqual(result["status"], "Succeeded")

    @patch("src.backend.services.action_execution_service.complete_action")
    @patch("src.backend.services.action_execution_service.claim_next_action")
    def test_worker_records_failure_for_retry(self, claim, complete) -> None:
        claim.return_value = {
            "action_id": "ACT1", "request_id": "SR1", "action_type": "RefundPayment",
            "payload": {}, "idempotency_key": "key-1",
        }
        complete.return_value = "Retrying"

        class BrokenConnector:
            def execute(self, *args):
                raise TimeoutError("payment timed out")

        result = process_one_action(BrokenConnector())
        self.assertEqual(result["status"], "Retrying")
        self.assertIn("timed out", result["error"])


if __name__ == "__main__":
    unittest.main()
