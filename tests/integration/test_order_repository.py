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
class PostgresOrderRepositoryIntegrationTests(unittest.TestCase):
    """Exercises PostgresOrderRepository against a real database with a
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
                    """TRUNCATE order_items, orders, customers, import_batches
                       RESTART IDENTITY CASCADE"""
                )
                cursor.execute(
                    "INSERT INTO import_batches (import_batch_id, pipeline_version, "
                    "dataset_anchor_date, source_datasets, row_counts) VALUES "
                    "('itest-batch', 'itest', '2026-08-31', '[]'::jsonb, '{}'::jsonb)"
                )
                cursor.execute(
                    "INSERT INTO customers (customer_id, vip_level, canceled_order_count, "
                    "import_batch_id) VALUES ('CUS-IT-001', 'Gold', 0, 'itest-batch')"
                )
                # Two orders for the same customer (repeat-customer case),
                # one multi-item with a genuine rounding gap, one canceled.
                cursor.execute(
                    "INSERT INTO orders (order_id, customer_id, order_status, payment_status, "
                    "refund_status, order_date, shipped_date, delivered_date, amount, "
                    "rounding_adjustment, import_batch_id) VALUES "
                    "('ORD-IT-001', 'CUS-IT-001', 'delivered', 'Paid', NULL, "
                    "'2026-01-10', '2026-01-11', '2026-01-15', 66.12, -0.01, 'itest-batch'),"
                    "('ORD-IT-002', 'CUS-IT-001', 'canceled', 'Paid', '已完成', "
                    "'2026-02-01', NULL, NULL, 40.00, 0, 'itest-batch'),"
                    "('ORD-IT-003', 'CUS-IT-001', 'processing', 'Paid', NULL, "
                    "'2026-03-01', NULL, NULL, 20.00, 0, 'itest-batch'),"
                    "('ORD-IT-004', 'CUS-IT-001', 'invoiced', 'Paid', NULL, "
                    "'2026-03-05', NULL, NULL, 15.00, 0, 'itest-batch')"
                )
                cursor.execute(
                    "INSERT INTO order_items (order_item_id, order_id, product_id, sku_id, "
                    "sku_name, unit_price, quantity, discount_amount, paid_amount) VALUES "
                    "('OI-IT-001', 'ORD-IT-001', 'PRO-IT-001', 'SKU-1', 'default', 33.74, 1, 0, 33.74),"
                    "('OI-IT-002', 'ORD-IT-001', 'PRO-IT-002', 'SKU-2', 'default', 32.39, 1, 0, 32.39),"
                    "('OI-IT-003', 'ORD-IT-002', 'PRO-IT-003', 'SKU-3', 'default', 40.00, 1, 0, 40.00)"
                )
            connection.commit()

    def _repo(self):
        from src.backend.repositories.order_repository import PostgresOrderRepository
        return PostgresOrderRepository(TEST_DATABASE_URL)

    def test_get_by_id_returns_the_row(self):
        order = self._repo().get_by_id("ORD-IT-001")
        self.assertEqual(order.customer_id, "CUS-IT-001")
        self.assertIsInstance(order.amount, Decimal)

    def test_get_by_id_raises_not_found(self):
        from src.backend.repositories.order_repository import OrderNotFoundError
        with self.assertRaises(OrderNotFoundError):
            self._repo().get_by_id("ORD-IT-DOES-NOT-EXIST")

    def test_list_by_customer_id_returns_all_four_orders(self):
        orders = self._repo().list_by_customer_id("CUS-IT-001")
        self.assertEqual(len(orders), 4)

    def test_order_status_values_pass_through_unmodified(self):
        orders = {o.order_id: o for o in self._repo().list_by_customer_id("CUS-IT-001")}
        self.assertEqual(orders["ORD-IT-001"].order_status, "delivered")
        self.assertEqual(orders["ORD-IT-002"].order_status, "canceled")
        self.assertEqual(orders["ORD-IT-003"].order_status, "processing")
        self.assertEqual(orders["ORD-IT-004"].order_status, "invoiced")

    def test_get_order_items_returns_multi_item_order_in_full(self):
        items = self._repo().get_order_items("ORD-IT-001")
        self.assertEqual(len(items), 2)
        prices = sorted(i.unit_price for i in items)
        self.assertEqual(prices, [Decimal("32.39"), Decimal("33.74")])

    def test_get_order_with_items_reconciles_amount_with_rounding_adjustment(self):
        combo = self._repo().get_order_with_items("ORD-IT-001")
        item_sum = sum(i.paid_amount for i in combo.items)
        self.assertEqual(item_sum, Decimal("66.13"))
        # order.amount (66.12) != raw item sum (66.13) -- the gap is exactly
        # rounding_adjustment, not silently dropped
        self.assertEqual(combo.order.amount - combo.order.rounding_adjustment, item_sum)

    def test_get_order_with_items_raises_not_found(self):
        from src.backend.repositories.order_repository import OrderNotFoundError
        with self.assertRaises(OrderNotFoundError):
            self._repo().get_order_with_items("ORD-IT-DOES-NOT-EXIST")

    def test_get_order_facts_counts_items_and_quantity_correctly(self):
        facts = self._repo().get_order_facts("ORD-IT-001")
        self.assertEqual(facts.item_count, 2)
        self.assertEqual(facts.total_quantity, 2)

    def test_single_item_order_facts_are_correct(self):
        facts = self._repo().get_order_facts("ORD-IT-002")
        self.assertEqual(facts.item_count, 1)
        self.assertEqual(facts.order_status, "canceled")


if __name__ == "__main__":
    unittest.main()
