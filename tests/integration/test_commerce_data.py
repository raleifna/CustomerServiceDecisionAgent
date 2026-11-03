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
class CommerceDataIntegrationTests(unittest.TestCase):
    """V2 (Postgres-only) orders get a real product name, amount and customer
    in the staff workbench; fixture orders keep resolving from the JSON."""

    @classmethod
    def setUpClass(cls) -> None:
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        from db.migrations.apply import apply_all
        from src.backend.repositories.postgres_repository import setup_application_schema

        setup_application_schema(TEST_DATABASE_URL)
        apply_all(TEST_DATABASE_URL)
        cls.previous_database_url = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = TEST_DATABASE_URL

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.previous_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = cls.previous_database_url

    def setUp(self) -> None:
        import psycopg
        from psycopg.types.json import Json

        from src.backend.services import commerce_data

        commerce_data._v2_product_card.cache_clear()
        with psycopg.connect(TEST_DATABASE_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """TRUNCATE data_provenance, order_items, orders, customers, products,
                              import_batches RESTART IDENTITY CASCADE"""
                )
                cursor.execute(
                    "INSERT INTO import_batches (import_batch_id, pipeline_version, "
                    "dataset_anchor_date, source_datasets, row_counts) VALUES "
                    "('itest-batch', 'itest', '2026-08-31', '[]'::jsonb, '{}'::jsonb)"
                )
                cursor.execute(
                    "INSERT INTO products (product_id, category, price, is_fragile, is_customized, import_batch_id) "
                    "VALUES ('PRO-IT-001', 'telephony', 99.50, FALSE, FALSE, 'itest-batch'),"
                    "('PRO-IT-002', 'mystery_category', 10, FALSE, FALSE, 'itest-batch')"
                )
                cursor.execute(
                    "INSERT INTO data_provenance (entity_type, entity_id, field_name, source_type, "
                    "source_dataset, source_record_id, transformation, pipeline_version, metadata) VALUES "
                    "('product', 'PRO-IT-001', NULL, 'SYNTHETIC_ENRICHMENT', 'Amazon Reviews 2023', "
                    "'B0ITEST', 'constrained_random_assignment_within_category', 'itest', %s)",
                    (Json({"amazon_title": "Clear Phone Case for Galaxy Z Flip"}),),
                )
                cursor.execute(
                    "INSERT INTO customers (customer_id, vip_level, register_date, canceled_order_count, import_batch_id) "
                    "VALUES ('CUS-IT-001', 'Bronze', NULL, 2, 'itest-batch')"
                )
                cursor.execute(
                    "INSERT INTO orders (order_id, customer_id, order_status, payment_status, order_date, "
                    "delivered_date, amount, import_batch_id) VALUES "
                    "('ORD-IT-001', 'CUS-IT-001', 'delivered', 'Paid', '2026-02-23', '2026-03-14', 99.50, 'itest-batch'),"
                    "('ORD-IT-002', 'CUS-IT-001', 'delivered', 'Paid', '2026-02-23', '2026-03-14', 10, 'itest-batch')"
                )
                cursor.execute(
                    "INSERT INTO order_items (order_item_id, order_id, product_id, sku_id, sku_name, "
                    "unit_price, quantity, discount_amount, paid_amount) VALUES "
                    "('OI-IT-001', 'ORD-IT-001', 'PRO-IT-001', 'SKU-1', '默认规格', 99.50, 1, 0, 99.50),"
                    "('OI-IT-002', 'ORD-IT-002', 'PRO-IT-002', 'SKU-2', '默认规格', 10, 1, 0, 10)"
                )
            connection.commit()

    def test_v2_order_customer_and_product_resolve_from_postgres(self) -> None:
        from src.backend.services.commerce_data import get_customer, get_order, product_card_for_order

        order = get_order("ORD-IT-001")
        self.assertEqual(order["amount"], 99.5)
        self.assertEqual(order["delivered_date"], "2026-03-14")
        card = product_card_for_order("ORD-IT-001", order, include_v2=True)
        self.assertEqual(card["title"], "Clear Phone Case for Galaxy Z Flip")
        self.assertEqual(card["category"], "手机通讯")
        self.assertIn("V2 导入", card["tags"])

        customer = get_customer("CUS-IT-001")
        self.assertEqual(customer["vip_level"], "Bronze")
        self.assertIsNone(customer["register_date"])
        self.assertEqual(customer["refund_history_count"], 0)
        self.assertEqual(customer["canceled_order_count"], 2)

    def test_v2_products_need_the_staff_flag(self) -> None:
        from src.backend.services.commerce_data import get_order, product_card_for_order

        self.assertIsNone(product_card_for_order("ORD-IT-001", get_order("ORD-IT-001")))

    def test_v2_customer_orders_list_from_postgres(self) -> None:
        from src.backend.services.commerce_data import list_customer_orders

        self.assertEqual([o["order_id"] for o in list_customer_orders("CUS-IT-001")], ["ORD-IT-001", "ORD-IT-002"])

    def test_untitled_unknown_category_falls_back_to_a_generic_name(self) -> None:
        from src.backend.services.commerce_data import get_order, product_card_for_order

        card = product_card_for_order("ORD-IT-002", get_order("ORD-IT-002"), include_v2=True)
        self.assertEqual(card["title"], "mystery_category商品 PRO-IT-002")
        self.assertEqual(card["icon"], "package")

    def test_unknown_ids_return_none(self) -> None:
        from src.backend.services.commerce_data import get_customer, get_order, product_card_for_order

        self.assertIsNone(get_order("ORD-IT-404"))
        self.assertIsNone(product_card_for_order("ORD-IT-404", None, include_v2=True))
        self.assertIsNone(get_customer("CUS-IT-404"))

    def test_fixture_orders_still_use_the_storefront_catalog(self) -> None:
        from src.backend.repositories.mock_db_repository import load_table
        from src.backend.services.commerce_data import get_order, product_card_for_order

        fixture = load_table("orders.json")[0]
        order = get_order(fixture["order_id"])
        self.assertEqual(order, fixture)
        self.assertEqual(product_card_for_order(fixture["order_id"], order, include_v2=True)["product_id"], fixture["product_id"])


if __name__ == "__main__":
    unittest.main()
