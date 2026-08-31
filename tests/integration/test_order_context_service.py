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
class PostgresOrderContextServiceIntegrationTests(unittest.TestCase):
    """Exercises OrderContextService against a real database with a small,
    hand-inserted fixture -- distinct from and never mixed with the
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
                    """TRUNCATE payments, shipment_events, shipments, order_items,
                       orders, products, customers, import_batches
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
                    "INSERT INTO products (product_id, category, price, is_fragile, "
                    "is_customized, import_batch_id) VALUES "
                    "('PRO-IT-001', 'electronics', 100.00, FALSE, FALSE, 'itest-batch'),"
                    "('PRO-IT-002', 'toys', 50.00, FALSE, FALSE, 'itest-batch')"
                )
                cursor.execute(
                    "INSERT INTO orders (order_id, customer_id, order_status, payment_status, "
                    "order_date, shipped_date, delivered_date, amount, rounding_adjustment, "
                    "import_batch_id) VALUES "
                    "('ORD-IT-FULL', 'CUS-IT-001', 'delivered', 'Paid', '2026-01-01', "
                    "'2026-01-02', '2026-01-05', 150.00, 0, 'itest-batch'),"
                    "('ORD-IT-CANCEL', 'CUS-IT-001', 'canceled', 'Paid', '2026-02-01', "
                    "NULL, NULL, 100.00, 0, 'itest-batch'),"
                    "('ORD-IT-NOSHIP', 'CUS-IT-001', 'processing', 'Paid', '2026-03-01', "
                    "NULL, NULL, 100.00, 0, 'itest-batch')"
                )
                cursor.execute(
                    "INSERT INTO order_items (order_item_id, order_id, product_id, sku_id, "
                    "sku_name, unit_price, quantity, discount_amount, paid_amount) VALUES "
                    "('OI-IT-001', 'ORD-IT-FULL', 'PRO-IT-001', 'SKU-1', 'default', 100.00, 1, 0, 100.00),"
                    "('OI-IT-002', 'ORD-IT-FULL', 'PRO-IT-002', 'SKU-2', 'default', 50.00, 1, 0, 50.00),"
                    "('OI-IT-003', 'ORD-IT-CANCEL', 'PRO-IT-001', 'SKU-1', 'default', 100.00, 1, 0, 100.00),"
                    "('OI-IT-004', 'ORD-IT-NOSHIP', 'PRO-IT-001', 'SKU-1', 'default', 100.00, 1, 0, 100.00)"
                )
                cursor.execute(
                    "INSERT INTO shipments (shipment_id, order_id, carrier, shipped_at, "
                    "delivered_at, estimated_delivery_date, shipment_status, import_batch_id) VALUES "
                    "('SHP-IT-FULL', 'ORD-IT-FULL', '第三方物流', '2026-01-02', '2026-01-05', "
                    "'2026-01-06', 'Delivered', 'itest-batch'),"
                    "('SHP-IT-CANCEL', 'ORD-IT-CANCEL', NULL, NULL, NULL, '2026-02-10', "
                    "'Canceled', 'itest-batch')"
                    # NOTE: ORD-IT-NOSHIP deliberately has NO shipment row --
                    # this is the "no shipment record" warning path.
                )
                cursor.execute(
                    "INSERT INTO payments (payment_id, order_id, payment_sequential, amount, "
                    "currency, payment_method, payment_installments, payment_reference_date, "
                    "import_batch_id) VALUES "
                    "('PAY-IT-001', 'ORD-IT-FULL', 1, 100.00, 'CNY', '代金券', 1, '2026-01-01', 'itest-batch'),"
                    "('PAY-IT-002', 'ORD-IT-FULL', 2, 50.00, 'CNY', '信用卡', 3, '2026-01-01', 'itest-batch')"
                    # NOTE: ORD-IT-CANCEL and ORD-IT-NOSHIP deliberately have
                    # NO payment rows -- the "no payment records" warning path.
                )
            connection.commit()

    def _service(self):
        from src.backend.repositories.order_repository import PostgresOrderRepository
        from src.backend.repositories.payment_repository import PostgresPaymentRepository
        from src.backend.repositories.product_repository import PostgresProductRepository
        from src.backend.repositories.shipment_repository import PostgresShipmentRepository
        from src.backend.services.order_context_service import OrderContextService

        return OrderContextService(
            order_repository=PostgresOrderRepository(TEST_DATABASE_URL),
            product_repository=PostgresProductRepository(TEST_DATABASE_URL),
            shipment_repository=PostgresShipmentRepository(TEST_DATABASE_URL),
            payment_repository=PostgresPaymentRepository(TEST_DATABASE_URL),
        )

    def test_full_context_reconciles_items_shipment_and_split_payment(self):
        ctx = self._service().get_order_context("ORD-IT-FULL", as_of_date=date(2026, 6, 1))
        self.assertEqual(len(ctx.items), 2)
        self.assertEqual(ctx.shipment.shipment_status, "Delivered")
        self.assertEqual(len(ctx.payments), 2)
        self.assertTrue(ctx.payment_facts.has_split_payment)
        self.assertEqual(ctx.consistency.order_amount_reconciled, True)
        self.assertEqual(ctx.consistency.payment_amount_reconciled, True)
        self.assertEqual(ctx.warnings, [])

    def test_canceled_never_shipped_order_returns_shipment_with_is_shipped_false(self):
        ctx = self._service().get_order_context("ORD-IT-CANCEL", as_of_date=date(2026, 6, 1))
        self.assertIsNotNone(ctx.shipment)  # the fulfillment record exists
        self.assertEqual(ctx.shipment.shipment_status, "Canceled")
        self.assertFalse(ctx.shipment_facts.is_shipped)
        # and payments are missing -- a warning, not a false "refunded" claim
        self.assertEqual(ctx.payments, [])
        codes = {w.code for w in ctx.warnings}
        self.assertIn("NO_PAYMENT_RECORDS", codes)

    def test_missing_shipment_record_produces_a_warning_not_an_error(self):
        ctx = self._service().get_order_context("ORD-IT-NOSHIP", as_of_date=date(2026, 6, 1))
        self.assertIsNone(ctx.shipment)
        self.assertIsNone(ctx.shipment_facts)
        codes = {w.code for w in ctx.warnings}
        self.assertIn("NO_SHIPMENT_RECORD", codes)

    def test_integrity_error_for_order_item_with_missing_product(self):
        import psycopg
        from src.backend.services.order_context_service import OrderContextIntegrityError

        with psycopg.connect(TEST_DATABASE_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO order_items (order_item_id, order_id, product_id, sku_id, "
                    "sku_name, unit_price, quantity, discount_amount, paid_amount) VALUES "
                    "('OI-BROKEN', 'ORD-IT-FULL', 'PRO-DOES-NOT-EXIST', 'SKU-X', 'x', 1, 1, 0, 1)"
                )
            connection.commit()

        with self.assertRaises(OrderContextIntegrityError) as exc_info:
            self._service().get_order_context("ORD-IT-FULL", as_of_date=date(2026, 6, 1))
        self.assertIn("PRO-DOES-NOT-EXIST", exc_info.exception.missing_product_ids)

    def test_order_not_found_raises_context_not_found(self):
        from src.backend.services.order_context_service import OrderContextNotFoundError
        with self.assertRaises(OrderContextNotFoundError):
            self._service().get_order_context("ORD-IT-DOES-NOT-EXIST", as_of_date=date(2026, 6, 1))

    def test_products_are_batched_not_looped_per_item(self):
        from src.backend.repositories.product_repository import PostgresProductRepository

        calls = []
        original_list_by_ids = PostgresProductRepository.list_by_ids

        def spy_list_by_ids(self, product_ids):
            calls.append(list(product_ids))
            return original_list_by_ids(self, product_ids)

        PostgresProductRepository.list_by_ids = spy_list_by_ids
        try:
            self._service().get_order_context("ORD-IT-FULL", as_of_date=date(2026, 6, 1))
        finally:
            PostgresProductRepository.list_by_ids = original_list_by_ids

        self.assertEqual(len(calls), 1)  # exactly one batched call, not one per order_item
        self.assertEqual(sorted(calls[0]), ["PRO-IT-001", "PRO-IT-002"])

    def test_postgres_source_summary_marks_payment_reference_date_derived(self):
        ctx = self._service().get_order_context("ORD-IT-FULL", as_of_date=date(2026, 6, 1))
        self.assertIn("DERIVED", ctx.source_summary.payment_reference_date)


if __name__ == "__main__":
    unittest.main()
