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
class PostgresShipmentRepositoryIntegrationTests(unittest.TestCase):
    """Exercises PostgresShipmentRepository against a real database with a
    small, hand-inserted fixture -- distinct from and never mixed with the
    27-order JSON fixture or the 1128-order Olist/Amazon import batch."""

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
                    """TRUNCATE shipment_events, shipments, orders, customers, import_batches
                       RESTART IDENTITY CASCADE"""
                )
                cursor.execute(
                    "INSERT INTO import_batches (import_batch_id, pipeline_version, "
                    "dataset_anchor_date, source_datasets, row_counts) VALUES "
                    "('itest-batch', 'itest', '2026-08-31', '[]'::jsonb, '{}'::jsonb)"
                )
                cursor.execute(
                    "INSERT INTO customers (customer_id, vip_level, canceled_order_count, "
                    "import_batch_id) VALUES ('CUS-IT-001', 'Gold', 1, 'itest-batch')"
                )
                cursor.execute(
                    "INSERT INTO orders (order_id, customer_id, order_status, payment_status, "
                    "order_date, amount, import_batch_id) VALUES "
                    "('ORD-IT-CANCEL', 'CUS-IT-001', 'canceled', 'Paid', '2026-01-01', 40.00, 'itest-batch'),"
                    "('ORD-IT-MULTI', 'CUS-IT-001', 'shipped', 'Paid', '2026-01-01', 60.00, 'itest-batch')"
                )
                # a canceled order that was NEVER shipped -- shipment record
                # exists per the "fulfillment record, not package" semantic,
                # but is_shipped must read False from shipped_at, not from
                # "does this row exist".
                cursor.execute(
                    "INSERT INTO shipments (shipment_id, order_id, carrier, shipped_at, "
                    "delivered_at, estimated_delivery_date, shipment_status, import_batch_id) VALUES "
                    "('SHP-IT-CANCEL', 'ORD-IT-CANCEL', NULL, NULL, NULL, '2026-01-10', 'Canceled', 'itest-batch'),"
                    "('SHP-IT-MULTI', 'ORD-IT-MULTI', '第三方物流', '2026-01-02', NULL, '2026-01-15', 'Overdue', 'itest-batch')"
                )
                # three events on the same shipment, two sharing the same
                # event_time -- ordering must be stable (event_time, event_id)
                cursor.execute(
                    "INSERT INTO shipment_events (event_id, shipment_id, event_type, event_time, "
                    "resolved, source_type, derivation) VALUES "
                    "('EVT-IT-003', 'SHP-IT-MULTI', 'third', '2026-01-20', TRUE, 'DERIVED', 'third note'),"
                    "('EVT-IT-001', 'SHP-IT-MULTI', 'first-same-day', '2026-01-16', FALSE, 'DERIVED', 'first note'),"
                    "('EVT-IT-002', 'SHP-IT-MULTI', 'second-same-day', '2026-01-16', FALSE, 'SYNTHETIC_EVENT', 'second note')"
                )
            connection.commit()

    def _repo(self):
        from src.backend.repositories.shipment_repository import PostgresShipmentRepository
        return PostgresShipmentRepository(TEST_DATABASE_URL)

    def test_canceled_never_shipped_order_has_a_shipment_row_but_is_shipped_false(self):
        shipment = self._repo().get_by_order_id("ORD-IT-CANCEL")
        self.assertEqual(shipment.shipment_status, "Canceled")
        self.assertIsNone(shipment.shipped_at)
        facts = self._repo().get_shipment_facts("ORD-IT-CANCEL", as_of_date=date(2026, 6, 1))
        self.assertFalse(facts.is_shipped)
        self.assertFalse(facts.is_delivered)
        self.assertFalse(facts.is_overdue)  # canceled orders are never "overdue"

    def test_get_by_id_raises_shipment_not_found_distinct_from_no_shipment_for_order(self):
        from src.backend.repositories.shipment_repository import (
            NoShipmentForOrderError,
            ShipmentNotFoundError,
        )
        with self.assertRaises(ShipmentNotFoundError):
            self._repo().get_by_id("SHP-IT-DOES-NOT-EXIST")
        with self.assertRaises(NoShipmentForOrderError):
            self._repo().get_by_order_id("ORD-IT-DOES-NOT-EXIST")

    def test_multi_event_shipment_returns_all_events(self):
        events = self._repo().get_events("SHP-IT-MULTI")
        self.assertEqual(len(events), 3)

    def test_same_day_events_keep_stable_deterministic_order(self):
        events = self._repo().get_events("SHP-IT-MULTI")
        # both 2026-01-16 events must sort before the 2026-01-20 one, and
        # between themselves must break the tie by event_id, every time
        self.assertEqual(
            [e.event_id for e in events],
            ["EVT-IT-001", "EVT-IT-002", "EVT-IT-003"],
        )

    def test_get_shipment_facts_latest_event_and_unresolved_count(self):
        facts = self._repo().get_shipment_facts("ORD-IT-MULTI", as_of_date=date(2026, 2, 1))
        self.assertEqual(facts.latest_event.event_id, "EVT-IT-003")
        self.assertEqual(facts.unresolved_exception_count, 2)  # EVT-IT-001 and EVT-IT-002

    def test_get_shipment_with_events_combines_both(self):
        combo = self._repo().get_shipment_with_events("ORD-IT-MULTI")
        self.assertEqual(combo.shipment.shipment_id, "SHP-IT-MULTI")
        self.assertEqual(len(combo.events), 3)


if __name__ == "__main__":
    unittest.main()
