import os
import unittest
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()
database_name = urlparse(TEST_DATABASE_URL).path.rsplit("/", 1)[-1] if TEST_DATABASE_URL else ""
SAFE_TEST_DATABASE = bool(TEST_DATABASE_URL and "test" in database_name.lower())


@unittest.skipUnless(SAFE_TEST_DATABASE, "Set TEST_DATABASE_URL to a dedicated database whose name contains 'test'")
class ScheduledJobsIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.previous = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = TEST_DATABASE_URL
        from src.backend.repositories.postgres_repository import setup_application_schema
        setup_application_schema(TEST_DATABASE_URL)

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = cls.previous

    def setUp(self) -> None:
        self.now = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
        with self._cursor() as cursor:
            cursor.execute(
                "TRUNCATE live_messages, human_handoffs, return_orders, service_request_items, "
                "service_requests, order_items, audit_logs RESTART IDENTITY CASCADE"
            )
            cursor.execute(
                "INSERT INTO order_items (order_item_id, order_id, product_id, sku_id, sku_name, unit_price, quantity, paid_amount) "
                "VALUES ('OI-S-1', 'ORD-S-1', 'PRO0001', 'SKU', '默认', 99, 1, 99)"
            )

    def _cursor(self):
        import psycopg
        from psycopg.rows import dict_row

        class _Ctx:
            def __enter__(inner):
                inner.connection = psycopg.connect(TEST_DATABASE_URL, row_factory=dict_row, autocommit=True)
                return inner.connection.cursor()

            def __exit__(inner, *exc):
                inner.connection.close()

        return _Ctx()

    def _request(self, request_id: str, status: str, **columns) -> None:
        extra_cols = "".join(f", {key}" for key in columns)
        extra_vals = "".join(", %s" for _ in columns)
        with self._cursor() as cursor:
            cursor.execute(
                f"INSERT INTO service_requests (request_id, order_id, customer_id, request_type, request_reason, "
                f"requested_quantity, request_date, workflow_status, decision{extra_cols}) "
                f"VALUES (%s, 'ORD-S-1', 'CUS0001', 'Refund', '不想要了', 1, '2026-10-20', %s, 'Approve'{extra_vals})",
                (request_id, status, *columns.values()),
            )

    def _return(self, return_id: str, request_id: str, created_at: datetime) -> None:
        with self._cursor() as cursor:
            cursor.execute(
                "INSERT INTO return_orders (return_order_id, request_id, order_id, customer_id, order_item_id, "
                "quantity, refundable_amount, workflow_status, created_at) "
                "VALUES (%s, %s, 'ORD-S-1', 'CUS0001', 'OI-S-1', 1, 99, 'AwaitingShipment', %s)",
                (return_id, request_id, created_at),
            )

    def _one(self, sql: str, *params):
        with self._cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchone()

    def test_unshipped_returns_expire_after_the_configured_days(self) -> None:
        from src.backend.services.scheduled_jobs import expire_unshipped_returns
        self._request("SR-OLD", "ReturnAwaitingShipment")
        self._request("SR-NEW", "ReturnAwaitingShipment")
        self._return("RET-OLD", "SR-OLD", self.now - timedelta(days=8))
        self._return("RET-NEW", "SR-NEW", self.now - timedelta(days=2))

        self.assertEqual(expire_unshipped_returns(self.now), ["RET-OLD"])
        self.assertEqual(self._one("SELECT workflow_status FROM return_orders WHERE return_order_id='RET-OLD'")["workflow_status"], "Expired")
        old = self._one("SELECT workflow_status, decision, approved_amount, resolution_notes FROM service_requests WHERE request_id='SR-OLD'")
        self.assertEqual((old["workflow_status"], old["decision"], old["approved_amount"]), ("Closed", "NoActionRequired", None))
        self.assertIn("未寄回", old["resolution_notes"])
        self.assertEqual(self._one("SELECT workflow_status FROM service_requests WHERE request_id='SR-NEW'")["workflow_status"], "ReturnAwaitingShipment")
        self.assertEqual(self._one("SELECT count(*) AS n FROM audit_logs WHERE action='ExpireUnshippedReturn'")["n"], 1)
        # Idempotent: a second run finds nothing more to do.
        self.assertEqual(expire_unshipped_returns(self.now), [])

    def test_expired_leases_are_released_and_versioned(self) -> None:
        from src.backend.services.scheduled_jobs import release_expired_leases
        self._request("SR-L1", "EscalatedToHuman", assigned_to="agent_a", lease_expires_at=self.now - timedelta(minutes=1), row_version=3)
        self._request("SR-L2", "EscalatedToHuman", assigned_to="agent_b", lease_expires_at=self.now + timedelta(minutes=5), row_version=3)
        self.assertEqual(release_expired_leases(self.now), ["SR-L1"])
        released = self._one("SELECT assigned_to, row_version FROM service_requests WHERE request_id='SR-L1'")
        self.assertEqual((released["assigned_to"], released["row_version"]), (None, 4))
        self.assertEqual(self._one("SELECT assigned_to FROM service_requests WHERE request_id='SR-L2'")["assigned_to"], "agent_b")

    def test_idle_and_unanswered_live_chats_end(self) -> None:
        from src.backend.services.scheduled_jobs import end_idle_live_chats
        with self._cursor() as cursor:
            cursor.execute(
                "INSERT INTO human_handoffs (handoff_id, customer_id, reason, workflow_status, staff_id, created_at, last_activity_at) VALUES "
                "('HOF-IDLE', 'CUS0001', 'x', 'Active', 'agent_a', %s, %s),"
                "('HOF-BUSY', 'CUS0002', 'x', 'Active', 'agent_a', %s, %s),"
                "('HOF-QUEUED', 'CUS0003', 'x', 'Waiting', NULL, %s, %s)",
                (self.now - timedelta(hours=2), self.now - timedelta(minutes=45),
                 self.now - timedelta(hours=2), self.now - timedelta(minutes=5),
                 self.now - timedelta(hours=30), self.now - timedelta(hours=30)),
            )
        self.assertEqual(sorted(end_idle_live_chats(self.now)), ["HOF-IDLE", "HOF-QUEUED"])
        self.assertEqual(self._one("SELECT workflow_status FROM human_handoffs WHERE handoff_id='HOF-BUSY'")["workflow_status"], "Active")
        queued = self._one("SELECT content FROM live_messages WHERE handoff_id='HOF-QUEUED'")
        self.assertIn("繁忙", queued["content"])

    def test_run_all_reports_every_job(self) -> None:
        from src.backend.services.scheduled_jobs import JOBS, run_all
        self.assertEqual(set(run_all(self.now)), set(JOBS))


if __name__ == "__main__":
    unittest.main()
