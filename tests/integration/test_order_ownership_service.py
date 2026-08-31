import os
import unittest
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
class PostgresOrderOwnershipServiceIntegrationTests(unittest.TestCase):
    """OrderOwnershipService against a real PostgresOrderRepository -- the
    unit tests in tests/test_order_ownership_service.py only exercise the
    Fixture backend plus hand-built stubs for the error-translation paths."""

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
                    "TRUNCATE orders, customers, import_batches RESTART IDENTITY CASCADE"
                )
                cursor.execute(
                    "INSERT INTO import_batches (import_batch_id, pipeline_version, "
                    "dataset_anchor_date, source_datasets, row_counts) VALUES "
                    "('own-itest-batch', 'itest', '2026-08-31', '[]'::jsonb, '{}'::jsonb)"
                )
                cursor.execute(
                    "INSERT INTO customers (customer_id, vip_level, canceled_order_count, "
                    "import_batch_id) VALUES ('CUS-OWN-001', 'Gold', 0, 'own-itest-batch')"
                )
                cursor.execute(
                    "INSERT INTO orders (order_id, customer_id, order_status, payment_status, "
                    "order_date, amount, rounding_adjustment, import_batch_id) VALUES "
                    "('ORD-OWN-001', 'CUS-OWN-001', 'delivered', 'Paid', '2026-01-01', "
                    "100.00, 0, 'own-itest-batch')"
                )
            connection.commit()

    def _service(self):
        from src.backend.repositories.order_repository import PostgresOrderRepository
        from src.backend.services.order_ownership_service import OrderOwnershipService

        return OrderOwnershipService(PostgresOrderRepository(TEST_DATABASE_URL))

    def test_matching_customer_is_authorized(self):
        from src.backend.services.order_ownership_service import AUTHORIZED

        result = self._service().verify_ownership("ORD-OWN-001", "CUS-OWN-001")
        self.assertEqual(result.status, AUTHORIZED)

    def test_mismatched_customer_is_denied(self):
        from src.backend.services.order_ownership_service import DENIED

        result = self._service().verify_ownership("ORD-OWN-001", "CUS-SOMEONE-ELSE")
        self.assertEqual(result.status, DENIED)

    def test_unknown_order_raises_order_not_found(self):
        from src.backend.services.tool_errors import ORDER_NOT_FOUND, ToolError

        with self.assertRaises(ToolError) as exc_info:
            self._service().verify_ownership("ORD-DOES-NOT-EXIST", "CUS-OWN-001")
        self.assertEqual(exc_info.exception.code, ORDER_NOT_FOUND)


if __name__ == "__main__":
    unittest.main()
