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
class PostgresProductRepositoryIntegrationTests(unittest.TestCase):
    """Exercises PostgresProductRepository against a real database with a
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
        from psycopg.types.json import Json

        with psycopg.connect(TEST_DATABASE_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """TRUNCATE data_provenance, order_items, products, import_batches
                       RESTART IDENTITY CASCADE"""
                )
                cursor.execute(
                    "INSERT INTO import_batches (import_batch_id, pipeline_version, "
                    "dataset_anchor_date, source_datasets, row_counts) VALUES "
                    "('itest-batch', 'itest', '2026-08-31', '[]'::jsonb, '{}'::jsonb)"
                )
                cursor.execute(
                    "INSERT INTO products (product_id, category, price, is_fragile, "
                    "is_customized, import_batch_id) VALUES "
                    "('PRO-IT-001', 'electronics', 199.99, TRUE, FALSE, 'itest-batch'),"
                    "('PRO-IT-002', 'toys', 49.50, FALSE, FALSE, 'itest-batch')"
                )
                cursor.execute(
                    "INSERT INTO data_provenance (entity_type, entity_id, field_name, "
                    "source_type, source_dataset, source_record_id, transformation, "
                    "pipeline_version, metadata) VALUES "
                    "('product', 'PRO-IT-001', NULL, 'SYNTHETIC_ENRICHMENT', "
                    "'Amazon Reviews 2023', 'B0ITESTASIN1', 'constrained_random_assignment_within_category', "
                    "'itest', %s), "
                    "('product', 'PRO-IT-001', 'price', 'DERIVED', NULL, NULL, "
                    "'USD_to_CNY_approx_7.10', 'itest', NULL)",
                    (Json({"amazon_title": "Test Widget", "price_band_applied": [0.3, 3.0]}),),
                )
                # a second order_item so we can assert order_items.unit_price is
                # never confused with products.price by any repository code path
                cursor.execute(
                    "INSERT INTO order_items (order_item_id, order_id, product_id, "
                    "sku_id, sku_name, unit_price, quantity, discount_amount, paid_amount) "
                    "VALUES ('OI-IT-001', 'ORD-IT-DOESNT-MATTER', 'PRO-IT-001', "
                    "'SKU-IT-001', 'default', 555.00, 1, 0, 555.00)"
                )
            connection.commit()

    def _repo(self):
        from src.backend.repositories.product_repository import PostgresProductRepository
        return PostgresProductRepository(TEST_DATABASE_URL)

    def test_get_by_id_returns_catalog_price_not_any_order_item_price(self):
        product = self._repo().get_by_id("PRO-IT-001")
        self.assertEqual(product.category, "electronics")
        self.assertEqual(product.price, Decimal("199.99"))  # NOT the 555.00 order_items.unit_price
        self.assertTrue(product.is_fragile)

    def test_get_by_id_raises_not_found(self):
        from src.backend.repositories.product_repository import ProductNotFoundError
        with self.assertRaises(ProductNotFoundError):
            self._repo().get_by_id("PRO-IT-DOES-NOT-EXIST")

    def test_get_by_source_id_resolves_via_data_provenance(self):
        product = self._repo().get_by_source_id("B0ITESTASIN1")
        self.assertEqual(product.product_id, "PRO-IT-001")

    def test_get_by_source_id_not_found_for_unknown_asin(self):
        from src.backend.repositories.product_repository import ProductNotFoundError
        with self.assertRaises(ProductNotFoundError):
            self._repo().get_by_source_id("B0-NEVER-IMPORTED")

    def test_list_by_ids_batches_in_one_query(self):
        products = self._repo().list_by_ids(["PRO-IT-001", "PRO-IT-002"])
        self.assertEqual({p.product_id for p in products}, {"PRO-IT-001", "PRO-IT-002"})
        self.assertTrue(all(isinstance(p.price, Decimal) for p in products))

    def test_get_product_facts_matches_catalog_row(self):
        facts = self._repo().get_product_facts("PRO-IT-002")
        self.assertEqual(facts.category, "toys")
        self.assertEqual(facts.price, Decimal("49.50"))
        self.assertFalse(hasattr(facts, "refund_eligible"))

    def test_get_product_provenance_returns_structured_metadata_not_a_json_string(self):
        provenance = self._repo().get_product_provenance("PRO-IT-001")
        self.assertEqual(len(provenance.records), 2)
        entity_record = next(r for r in provenance.records if r.field_name is None)
        self.assertEqual(entity_record.source_type, "SYNTHETIC_ENRICHMENT")
        self.assertEqual(entity_record.source_record_id, "B0ITESTASIN1")
        self.assertIsInstance(entity_record.metadata, dict)  # not a raw JSON string
        self.assertEqual(entity_record.metadata["amazon_title"], "Test Widget")
        price_record = next(r for r in provenance.records if r.field_name == "price")
        self.assertEqual(price_record.source_type, "DERIVED")

    def test_get_product_provenance_raises_not_found_for_unknown_product(self):
        from src.backend.repositories.product_repository import ProductNotFoundError
        with self.assertRaises(ProductNotFoundError):
            self._repo().get_product_provenance("PRO-IT-DOES-NOT-EXIST")


if __name__ == "__main__":
    unittest.main()
