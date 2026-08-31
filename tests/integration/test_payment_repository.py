import os
import unittest
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
class PostgresPaymentRepositoryIntegrationTests(unittest.TestCase):
    """Exercises PostgresPaymentRepository against a real database with a
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
                    """TRUNCATE payments, orders, customers, import_batches
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
                    "('ORD-IT-SPLIT', 'CUS-IT-001', 'delivered', 'Paid', '2026-01-01', 150.00, 'itest-batch'),"
                    "('ORD-IT-SINGLE', 'CUS-IT-001', 'delivered', 'Paid', '2026-01-05', 50.00, 'itest-batch'),"
                    "('ORD-IT-CANCEL', 'CUS-IT-001', 'canceled', 'Paid', '2026-01-10', 30.00, 'itest-batch')"
                )
                # a split payment: voucher + credit card, out of insertion
                # order on purpose so the ORDER BY payment_sequential must
                # do the actual work of sorting them back into 1, 2
                cursor.execute(
                    "INSERT INTO payments (payment_id, order_id, payment_sequential, amount, "
                    "currency, payment_method, payment_installments, payment_reference_date, "
                    "import_batch_id) VALUES "
                    "('PAY-IT-002', 'ORD-IT-SPLIT', 2, 100.00, 'CNY', '信用卡', 8, '2026-01-01', 'itest-batch'),"
                    "('PAY-IT-001', 'ORD-IT-SPLIT', 1, 50.00, 'CNY', '代金券', 1, '2026-01-01', 'itest-batch'),"
                    "('PAY-IT-003', 'ORD-IT-SINGLE', 1, 50.00, 'CNY', '借记卡', NULL, NULL, 'itest-batch'),"
                    "('PAY-IT-004', 'ORD-IT-CANCEL', 1, 30.00, 'CNY', '信用卡', 1, '2026-01-10', 'itest-batch')"
                )
            connection.commit()

    def _repo(self):
        from src.backend.repositories.payment_repository import PostgresPaymentRepository
        return PostgresPaymentRepository(TEST_DATABASE_URL)

    def test_split_payment_returns_both_rows_ordered_by_payment_sequential(self):
        payments = self._repo().list_by_order_id("ORD-IT-SPLIT")
        self.assertEqual([p.payment_id for p in payments], ["PAY-IT-001", "PAY-IT-002"])
        self.assertEqual([p.payment_sequential for p in payments], [1, 2])

    def test_split_payment_preserves_distinct_methods_and_installments(self):
        payments = self._repo().list_by_order_id("ORD-IT-SPLIT")
        self.assertEqual(payments[0].payment_method, "代金券")
        self.assertEqual(payments[1].payment_method, "信用卡")
        self.assertEqual(payments[1].payment_installments, 8)  # billing installments, not a payment count

    def test_get_payment_facts_sums_split_payment_correctly(self):
        facts = self._repo().get_payment_facts("ORD-IT-SPLIT")
        self.assertEqual(facts.payment_count, 2)
        self.assertTrue(facts.has_split_payment)
        self.assertEqual(facts.total_amount, Decimal("150.00"))
        self.assertEqual(facts.payment_methods, ["代金券", "信用卡"])

    def test_single_payment_order_facts(self):
        facts = self._repo().get_payment_facts("ORD-IT-SINGLE")
        self.assertEqual(facts.payment_count, 1)
        self.assertFalse(facts.has_split_payment)

    def test_null_payment_reference_date_handled_without_error(self):
        payment = self._repo().get_by_id("PAY-IT-003")
        self.assertIsNone(payment.payment_reference_date)

    def test_canceled_order_payment_facts_carry_no_refund_judgment(self):
        # the payment row for a canceled order is returned as-is -- this
        # repository makes no claim about whether it was refunded
        facts = self._repo().get_payment_facts("ORD-IT-CANCEL")
        self.assertEqual(facts.payment_count, 1)
        self.assertEqual(facts.total_amount, Decimal("30.00"))
        self.assertFalse(hasattr(facts, "refund_status"))
        self.assertFalse(hasattr(facts, "is_refunded"))

    def test_get_by_id_raises_payment_not_found_distinct_from_no_payments_for_order(self):
        from src.backend.repositories.payment_repository import (
            NoPaymentsForOrderError,
            PaymentNotFoundError,
        )
        with self.assertRaises(PaymentNotFoundError):
            self._repo().get_by_id("PAY-IT-DOES-NOT-EXIST")
        with self.assertRaises(NoPaymentsForOrderError):
            self._repo().list_by_order_id("ORD-IT-DOES-NOT-EXIST")


if __name__ == "__main__":
    unittest.main()
