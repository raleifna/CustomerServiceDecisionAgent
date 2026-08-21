import os
import threading
import unittest
from urllib.parse import urlparse


TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()
database_name = urlparse(TEST_DATABASE_URL).path.rsplit("/", 1)[-1] if TEST_DATABASE_URL else ""
SAFE_TEST_DATABASE = bool(TEST_DATABASE_URL and "test" in database_name.lower())


@unittest.skipUnless(
    SAFE_TEST_DATABASE,
    "Set TEST_DATABASE_URL to a dedicated database whose name contains 'test'",
)
class PostgresLifecycleIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.previous_database_url = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = TEST_DATABASE_URL
        from src.backend.repositories.postgres_repository import setup_application_schema
        setup_application_schema(TEST_DATABASE_URL)

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.previous_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = cls.previous_database_url

    def setUp(self) -> None:
        import psycopg
        with psycopg.connect(TEST_DATABASE_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """TRUNCATE audit_logs, outbox_events, action_tasks,
                              decision_records, service_request_items,
                              service_requests, order_items
                       RESTART IDENTITY CASCADE"""
                )

    def _seed_order_item(self) -> None:
        import psycopg
        with psycopg.connect(TEST_DATABASE_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """INSERT INTO order_items (
                           order_item_id, order_id, product_id, sku_id, sku_name,
                           unit_price, quantity, paid_amount
                       ) VALUES ('OI-TEST-1', 'ORD-TEST-1', 'PRO-TEST-1',
                                 'SKU-TEST-1', '测试规格', 99, 2, 198)"""
                )

    def _create_request(self) -> dict:
        self._seed_order_item()
        from src.backend.repositories.service_request_repository import create_service_request
        record, created = create_service_request(
            order_id="ORD-TEST-1",
            customer_id="CUS-TEST-1",
            request_type="Refund",
            request_reason="质量问题",
            requested_quantity=1,
            order_item={
                "order_item_id": "OI-TEST-1", "order_id": "ORD-TEST-1",
                "product_id": "PRO-TEST-1", "sku_id": "SKU-TEST-1",
                "sku_name": "测试规格", "unit_price": 99, "quantity": 2,
                "discount_amount": 0, "paid_amount": 198,
                "shipped_product_id": "PRO-TEST-1", "shipped_quantity": 2,
            },
            order_item_count=1,
        )
        self.assertTrue(created)
        return record

    def test_request_and_item_relation_are_committed_together(self) -> None:
        record = self._create_request()
        import psycopg
        with psycopg.connect(TEST_DATABASE_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT order_item_id, requested_quantity FROM service_request_items WHERE request_id = %s",
                    (record["request_id"],),
                )
                self.assertEqual(cursor.fetchone(), ("OI-TEST-1", 1))

    def test_decision_action_and_outbox_are_idempotent(self) -> None:
        request = self._create_request()
        from src.backend.repositories.decision_lifecycle_repository import persist_decision_lifecycle
        final = {
            "request_id": request["request_id"], "order_id": "ORD-TEST-1",
            "customer_id": "CUS-TEST-1", "order_item_id": "OI-TEST-1",
            "request_type": "Refund", "request_reason": "质量问题",
            "decision": "Approve", "approved_quantity": 1,
            "approved_amount": 99, "decision_confidence": 0.9,
            "matched_rule_id": "quality_evidence", "reasons": ["verified"],
            "rule_hits": ["REFUND-003"],
        }
        first = persist_decision_lifecycle(final, input_snapshot={"same": True})
        second = persist_decision_lifecycle(final, input_snapshot={"same": True})
        self.assertEqual(first["decision_id"], second["decision_id"])
        import psycopg
        with psycopg.connect(TEST_DATABASE_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT count(*) FROM decision_records")
                self.assertEqual(cursor.fetchone()[0], 1)
                cursor.execute("SELECT count(*) FROM action_tasks")
                self.assertEqual(cursor.fetchone()[0], 1)
                cursor.execute("SELECT count(*) FROM outbox_events WHERE event_type = 'ActionTaskCreated'")
                self.assertEqual(cursor.fetchone()[0], 1)

    def test_missing_request_rolls_back_decision(self) -> None:
        from src.backend.repositories.decision_lifecycle_repository import persist_decision_lifecycle
        with self.assertRaises(RuntimeError):
            persist_decision_lifecycle(
                {"request_id": "MISSING", "request_type": "Refund", "request_reason": "质量问题",
                 "decision": "Reject", "approved_quantity": None, "approved_amount": None,
                 "decision_confidence": 1.0, "reasons": [], "rule_hits": []},
                input_snapshot={"missing": True},
            )
        import psycopg
        with psycopg.connect(TEST_DATABASE_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT count(*) FROM decision_records")
                self.assertEqual(cursor.fetchone()[0], 0)

    def test_two_workers_cannot_claim_the_same_action(self) -> None:
        request = self._create_request()
        from src.backend.repositories.decision_lifecycle_repository import (
            claim_next_action, persist_decision_lifecycle,
        )
        persist_decision_lifecycle(
            {"request_id": request["request_id"], "order_id": "ORD-TEST-1",
             "customer_id": "CUS-TEST-1", "order_item_id": "OI-TEST-1",
             "request_type": "Refund", "request_reason": "质量问题",
             "decision": "Approve", "approved_quantity": 1, "approved_amount": 99,
             "decision_confidence": 0.9, "reasons": [], "rule_hits": []},
            input_snapshot={"claim": True},
        )
        barrier = threading.Barrier(2)
        claimed = []

        def worker() -> None:
            barrier.wait()
            claimed.append(claim_next_action())

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sum(item is not None for item in claimed), 1)

    def test_two_staff_members_cannot_claim_the_same_request(self) -> None:
        request = self._create_request()
        import psycopg
        with psycopg.connect(TEST_DATABASE_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE service_requests SET workflow_status = 'EscalatedToHuman' WHERE request_id = %s",
                    (request["request_id"],),
                )
        from src.backend.repositories.service_request_repository import claim_service_request
        barrier = threading.Barrier(2)
        claimed = []

        def staff(staff_id: str) -> None:
            barrier.wait()
            claimed.append(claim_service_request(request["request_id"], staff_id=staff_id))

        threads = [
            threading.Thread(target=staff, args=("agent-a",)),
            threading.Thread(target=staff, args=("agent-b",)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sum(item is not None for item in claimed), 1)


if __name__ == "__main__":
    unittest.main()
