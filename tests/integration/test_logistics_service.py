import os
import unittest
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parents[2]

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()
database_name = urlparse(TEST_DATABASE_URL).path.rsplit("/", 1)[-1] if TEST_DATABASE_URL else ""
SAFE_TEST_DATABASE = bool(TEST_DATABASE_URL and "test" in database_name.lower())


@unittest.skipUnless(
    SAFE_TEST_DATABASE,
    "Set TEST_DATABASE_URL to a dedicated database whose name contains 'test'",
)
class PostgresLogisticsProviderIntegrationTests(unittest.TestCase):
    """PostgresLogisticsProvider must report tracking_stalled_over_72h /
    stalled_in_transit_over_72h as UNAVAILABLE against real V2 data even
    when a shipment has real, unresolved shipment_events -- their absence
    from the real event vocabulary proves nothing about whether the
    fixture-only concepts they represent occurred."""

    @classmethod
    def setUpClass(cls) -> None:
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        from src.backend.repositories.postgres_repository import setup_application_schema
        from db.migrations.apply import apply_all

        setup_application_schema(TEST_DATABASE_URL)
        apply_all(TEST_DATABASE_URL)

    def setUp(self) -> None:
        import psycopg

        with psycopg.connect(TEST_DATABASE_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "TRUNCATE shipment_events, shipments, orders, customers, "
                    "import_batches RESTART IDENTITY CASCADE"
                )
                cursor.execute(
                    "INSERT INTO import_batches (import_batch_id, pipeline_version, "
                    "dataset_anchor_date, source_datasets, row_counts) VALUES "
                    "('log-itest-batch', 'itest', '2026-08-31', '[]'::jsonb, '{}'::jsonb)"
                )
                cursor.execute(
                    "INSERT INTO customers (customer_id, vip_level, canceled_order_count, "
                    "import_batch_id) VALUES ('CUS-LOG-001', 'Gold', 0, 'log-itest-batch')"
                )
                cursor.execute(
                    "INSERT INTO orders (order_id, customer_id, order_status, payment_status, "
                    "order_date, shipped_date, estimated_delivery_date, amount, "
                    "rounding_adjustment, import_batch_id) VALUES "
                    "('ORD-LOG-001', 'CUS-LOG-001', 'shipped', 'Paid', '2026-01-01', "
                    "'2026-01-02', '2026-01-10', 100.00, 0, 'log-itest-batch')"
                )
                cursor.execute(
                    "INSERT INTO shipments (shipment_id, order_id, carrier, shipped_at, "
                    "delivered_at, estimated_delivery_date, shipment_status, import_batch_id) VALUES "
                    "('SHP-LOG-001', 'ORD-LOG-001', '第三方物流', '2026-01-02', NULL, "
                    "'2026-01-10', 'Overdue', 'log-itest-batch')"
                )
                cursor.execute(
                    "INSERT INTO shipment_events (event_id, shipment_id, event_type, event_time, "
                    "resolved, source_type, derivation) VALUES "
                    "('EVT-LOG-001', 'SHP-LOG-001', '超过预计送达时间仍未签收', '2026-01-10', "
                    "FALSE, 'DERIVED', 'test')"
                )
            connection.commit()

    def _provider(self):
        from src.backend.repositories.shipment_repository import PostgresShipmentRepository
        from src.backend.services.logistics_service import PostgresLogisticsProvider

        return PostgresLogisticsProvider(PostgresShipmentRepository(TEST_DATABASE_URL))

    def test_tracking_stalled_is_unavailable_despite_a_real_unresolved_event(self):
        facts = self._provider().get_logistics_facts("ORD-LOG-001", as_of_date=date(2026, 6, 1))
        self.assertEqual(facts.tracking_stalled_over_72h.availability, "UNAVAILABLE")
        self.assertIsNone(facts.tracking_stalled_over_72h.value)
        self.assertEqual(facts.stalled_in_transit_over_72h.availability, "UNAVAILABLE")

    def test_delivery_overdue_and_has_exception_are_real_and_available(self):
        facts = self._provider().get_logistics_facts("ORD-LOG-001", as_of_date=date(2026, 6, 1))
        self.assertEqual(facts.delivery_overdue.availability, "AVAILABLE")
        self.assertTrue(facts.delivery_overdue.value)
        self.assertEqual(facts.has_logistics_exception.availability, "AVAILABLE")
        self.assertTrue(facts.has_logistics_exception.value)
        self.assertTrue(facts.has_unresolved_exception.value)

    def test_exceptions_list_carries_the_real_event_through(self):
        facts = self._provider().get_logistics_facts("ORD-LOG-001", as_of_date=date(2026, 6, 1))
        self.assertEqual(len(facts.exceptions), 1)
        self.assertEqual(facts.exceptions[0].exception_type, "超过预计送达时间仍未签收")


if __name__ == "__main__":
    unittest.main()
