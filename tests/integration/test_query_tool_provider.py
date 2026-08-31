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
class PostgresQueryToolProviderIntegrationTests(unittest.TestCase):
    """PostgresQueryToolProvider must honestly report UNAVAILABLE for
    capabilities the real V2 data doesn't have (promised_ship_date,
    continuous tracking) instead of omitting the fields or faking values."""

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
                    """TRUNCATE shipment_events, shipments, order_items, orders,
                       products, customers, import_batches RESTART IDENTITY CASCADE"""
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
                cursor.execute(
                    "INSERT INTO products (product_id, category, price, is_fragile, "
                    "is_customized, import_batch_id) VALUES "
                    "('PRO-IT-001', 'electronics', 100.00, FALSE, FALSE, 'itest-batch')"
                )
                cursor.execute(
                    "INSERT INTO orders (order_id, customer_id, order_status, payment_status, "
                    "order_date, shipped_date, estimated_delivery_date, amount, "
                    "rounding_adjustment, import_batch_id) VALUES "
                    "('ORD-IT-001', 'CUS-IT-001', 'shipped', 'Paid', '2026-01-01', "
                    "'2026-01-02', '2026-01-10', 100.00, 0, 'itest-batch')"
                )
                cursor.execute(
                    "INSERT INTO order_items (order_item_id, order_id, product_id, sku_id, "
                    "sku_name, unit_price, quantity, discount_amount, paid_amount) VALUES "
                    "('OI-IT-001', 'ORD-IT-001', 'PRO-IT-001', 'SKU-1', 'default', 100.00, 1, 0, 100.00)"
                )
                cursor.execute(
                    "INSERT INTO shipments (shipment_id, order_id, carrier, shipped_at, "
                    "delivered_at, estimated_delivery_date, shipment_status, import_batch_id) VALUES "
                    "('SHP-IT-001', 'ORD-IT-001', '第三方物流', '2026-01-02', NULL, "
                    "'2026-01-10', 'Overdue', 'itest-batch')"
                )
                cursor.execute(
                    "INSERT INTO shipment_events (event_id, shipment_id, event_type, event_time, "
                    "resolved, source_type, derivation) VALUES "
                    "('EVT-IT-001', 'SHP-IT-001', '超过预计送达时间仍未签收', '2026-01-10', "
                    "FALSE, 'DERIVED', 'test')"
                )
            connection.commit()

    def _provider(self):
        from src.backend.repositories.order_repository import PostgresOrderRepository
        from src.backend.repositories.payment_repository import PostgresPaymentRepository
        from src.backend.repositories.product_repository import PostgresProductRepository
        from src.backend.repositories.shipment_repository import PostgresShipmentRepository
        from src.backend.services.order_context_service import OrderContextService
        from src.backend.services.query_tool_provider import PostgresQueryToolProvider

        service = OrderContextService(
            order_repository=PostgresOrderRepository(TEST_DATABASE_URL),
            product_repository=PostgresProductRepository(TEST_DATABASE_URL),
            shipment_repository=PostgresShipmentRepository(TEST_DATABASE_URL),
            payment_repository=PostgresPaymentRepository(TEST_DATABASE_URL),
        )
        return PostgresQueryToolProvider(service)

    def test_expedite_is_unavailable_with_reason_not_a_missing_field(self):
        result = self._provider().get_order_status("ORD-IT-001", as_of_date=date(2026, 6, 1))
        self.assertEqual(result["expedite"]["availability"], "UNAVAILABLE")
        self.assertEqual(result["expedite"]["reason"], "PROMISED_SHIP_DATE_NOT_PROVIDED")
        self.assertIsNone(result["expedite"]["can_offer"])  # None, not False
        self.assertIsNone(result["expedite"]["is_overdue"])  # None, not False -- can't judge

    def test_tracking_is_unavailable_but_real_facts_still_come_through(self):
        result = self._provider().get_logistics_status("ORD-IT-001", as_of_date=date(2026, 6, 1))
        self.assertEqual(result["tracking"]["availability"], "UNAVAILABLE")
        self.assertEqual(result["tracking"]["reason"], "SOURCE_DATA_NOT_PROVIDED")
        self.assertEqual(result["tracking"]["checkpoints"], [])
        # but the real overdue fact still comes through -- unavailability of
        # tracking doesn't blank out everything else
        self.assertTrue(result["is_overdue"])
        self.assertTrue(result["is_shipped"])
        self.assertFalse(result["is_delivered"])

    def test_logistics_chase_can_offer_true_for_a_real_overdue_shipment(self):
        result = self._provider().get_logistics_status("ORD-IT-001", as_of_date=date(2026, 6, 1))
        self.assertEqual(result["logistics_chase"]["availability"], "AVAILABLE")
        self.assertTrue(result["logistics_chase"]["can_offer"])

    def test_order_item_with_missing_product_raises_data_integrity_tool_error(self):
        from src.backend.services.tool_errors import DATA_INTEGRITY_ERROR, ToolError

        import psycopg

        with psycopg.connect(TEST_DATABASE_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO order_items (order_item_id, order_id, product_id, sku_id, "
                    "sku_name, unit_price, quantity, discount_amount, paid_amount) VALUES "
                    "('OI-IT-BROKEN', 'ORD-IT-001', 'PRO-DOES-NOT-EXIST', 'SKU-X', 'x', 1, 1, 0, 1)"
                )
            connection.commit()

        with self.assertRaises(ToolError) as exc_info:
            self._provider().get_order_status("ORD-IT-001", as_of_date=date(2026, 6, 1))
        self.assertEqual(exc_info.exception.code, DATA_INTEGRITY_ERROR)
        self.assertFalse(exc_info.exception.retryable)

    def test_unknown_order_id_raises_order_not_found_tool_error_from_logistics(self):
        from src.backend.services.tool_errors import ORDER_NOT_FOUND, ToolError

        with self.assertRaises(ToolError) as exc_info:
            self._provider().get_logistics_status("ORD-IT-DOES-NOT-EXIST", as_of_date=date(2026, 6, 1))
        self.assertEqual(exc_info.exception.code, ORDER_NOT_FOUND)


if __name__ == "__main__":
    unittest.main()
