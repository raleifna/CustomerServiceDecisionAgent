import os
import unittest
from datetime import date
from decimal import Decimal
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
class PostgresCustomerRepositoryIntegrationTests(unittest.TestCase):
    """Exercises PostgresCustomerRepository against a real database with a
    small, hand-inserted fixture of its own -- distinct from and never
    mixed with the 27-order JSON fixture or the 1128-order Olist/Amazon
    import batch."""

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
                    """TRUNCATE orders, customers, import_batches RESTART IDENTITY CASCADE"""
                )
                cursor.execute(
                    "INSERT INTO import_batches (import_batch_id, pipeline_version, "
                    "dataset_anchor_date, source_datasets, row_counts) VALUES "
                    "('itest-batch', 'itest', '2026-08-31', '[]'::jsonb, '{}'::jsonb)"
                )
                cursor.execute(
                    "INSERT INTO customers (customer_id, source_unique_id, vip_level, "
                    "register_date, canceled_order_count, import_batch_id) VALUES "
                    "('CUS-IT-001', 'olist-unique-001', 'Gold', NULL, 1, 'itest-batch')"
                )
                cursor.execute(
                    "INSERT INTO orders (order_id, customer_id, order_status, payment_status, "
                    "order_date, delivered_date, amount, import_batch_id) VALUES "
                    "('ORD-IT-001', 'CUS-IT-001', 'delivered', 'Paid', '2026-01-10', '2026-01-15', 100.00, 'itest-batch'),"
                    "('ORD-IT-002', 'CUS-IT-001', 'delivered', 'Paid', '2026-02-10', '2026-02-15', 200.00, 'itest-batch'),"
                    "('ORD-IT-003', 'CUS-IT-001', 'canceled', 'Paid', '2026-03-10', NULL, 50.00, 'itest-batch')"
                )
            connection.commit()

    def _repo(self):
        from src.backend.repositories.customer_repository import PostgresCustomerRepository
        return PostgresCustomerRepository(TEST_DATABASE_URL)

    def test_get_by_id_returns_the_row(self):
        customer = self._repo().get_by_id("CUS-IT-001")
        self.assertEqual(customer.vip_level, "Gold")
        self.assertEqual(customer.source_unique_id, "olist-unique-001")

    def test_get_by_id_raises_not_found(self):
        from src.backend.repositories.customer_repository import CustomerNotFoundError
        with self.assertRaises(CustomerNotFoundError):
            self._repo().get_by_id("CUS-IT-DOES-NOT-EXIST")

    def test_get_by_source_unique_id_finds_the_same_row(self):
        customer = self._repo().get_by_source_unique_id("olist-unique-001")
        self.assertEqual(customer.customer_id, "CUS-IT-001")

    def test_list_orders_summary_returns_all_three_orders_newest_first(self):
        orders = self._repo().list_orders_summary("CUS-IT-001")
        self.assertEqual([o.order_id for o in orders], ["ORD-IT-003", "ORD-IT-002", "ORD-IT-001"])
        self.assertTrue(all(isinstance(o.amount, Decimal) for o in orders))
        self.assertEqual(orders[0].order_status, "canceled")

    def test_risk_facts_exclude_canceled_orders_from_spend_and_completed_count(self):
        facts = self._repo().get_customer_risk_facts("CUS-IT-001")
        self.assertEqual(facts.total_order_count, 3)
        self.assertEqual(facts.completed_order_count, 2)  # canceled order doesn't count as delivered
        self.assertEqual(facts.total_spend, Decimal("300.00"))  # excludes the 50.00 canceled order
        self.assertEqual(facts.first_order_date, date(2026, 1, 10))
        self.assertEqual(facts.last_order_date, date(2026, 3, 10))
        self.assertEqual(facts.canceled_order_count, 1)  # from customers.canceled_order_count, not derived here


if __name__ == "__main__":
    unittest.main()
