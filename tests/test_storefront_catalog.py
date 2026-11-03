import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.backend.api.main import app
from src.backend.config.business_rules import load_business_rules
from src.backend.repositories.mock_db_repository import load_table
from src.backend.repositories.product_catalog_repository import (
    get_storefront_product_card,
    get_storefront_product_detail,
    list_storefront_products,
)


class StorefrontCatalogTests(unittest.TestCase):
    def test_every_product_fact_has_a_card(self) -> None:
        facts = load_table("products.json")
        cards = list_storefront_products()
        self.assertEqual({fact["product_id"] for fact in facts}, {card["product_id"] for card in cards})

    def test_catalog_only_covers_known_products(self) -> None:
        fact_ids = {fact["product_id"] for fact in load_table("products.json")}
        for row in load_table("product_catalog.json"):
            self.assertIn(row["product_id"], fact_ids)

    def test_decision_facts_come_from_products_json_not_catalog(self) -> None:
        for fact in load_table("products.json"):
            card = get_storefront_product_card(fact["product_id"])
            self.assertEqual(card["price"], float(fact["price"]))
            self.assertEqual(card["category"], fact["category"])
            self.assertEqual(card["is_fragile"], fact["is_fragile"])
            self.assertEqual(card["is_customized"], fact["is_customized"])

    def test_catalog_rows_do_not_author_decision_facts(self) -> None:
        for row in load_table("product_catalog.json"):
            for key in ("price", "category", "is_fragile", "is_customized"):
                self.assertNotIn(key, row, f"{row['product_id']} duplicates fact field {key}")

    def test_no_reason_promise_follows_policy(self) -> None:
        days = load_business_rules()["refund"]["no_reason_return_days"]
        for fact in load_table("products.json"):
            titles = [p["title"] for p in get_storefront_product_detail(fact["product_id"])["service_promises"]]
            if fact["is_customized"]:
                self.assertNotIn(f"{days}天无理由退货", titles)
                self.assertIn("定制商品", titles)
            else:
                self.assertIn(f"{days}天无理由退货", titles)
            self.assertEqual("易碎品保障" in titles, fact["is_fragile"])

    def test_filters_and_sorting(self) -> None:
        digital = list_storefront_products(category="数码")
        self.assertTrue(digital)
        self.assertTrue(all(card["category"] == "数码" for card in digital))
        prices = [card["price"] for card in list_storefront_products(sort="price_asc")]
        self.assertEqual(prices, sorted(prices))
        self.assertEqual(
            [card["product_id"] for card in list_storefront_products(query="耳机")],
            ["PRO0001"],
        )

    def test_unknown_product_returns_none(self) -> None:
        self.assertIsNone(get_storefront_product_card("PRO9999"))
        self.assertIsNone(get_storefront_product_detail("PRO9999"))


class StorefrontApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # No `with` block: the lifespan (checkpointer / Postgres setup) is not
        # needed for these read-only catalog routes.
        cls.client = TestClient(app)

    def test_product_list_and_detail(self) -> None:
        listing = self.client.get("/products", params={"category": "家居"})
        self.assertEqual(listing.status_code, 200)
        self.assertTrue(all(item["category"] == "家居" for item in listing.json()))

        detail = self.client.get("/products/PRO0001")
        self.assertEqual(detail.status_code, 200)
        body = detail.json()
        self.assertEqual(body["product_id"], "PRO0001")
        self.assertTrue(body["skus"])
        self.assertTrue(body["service_promises"])

        self.assertEqual(self.client.get("/products/PRO9999").status_code, 404)

    def test_categories(self) -> None:
        response = self.client.get("/product-categories")
        self.assertEqual(response.status_code, 200)
        total = sum(item["product_count"] for item in response.json())
        self.assertEqual(total, len(load_table("products.json")))

    def test_order_summary_and_detail_carry_product_card(self) -> None:
        order = load_table("orders.json")[0]
        customer_id, order_id = order["customer_id"], order["order_id"]
        with patch.dict(os.environ, {"DEMO_LOGIN_ENABLED": "true", "AUTH_SECRET": "storefront-test"}):
            token = self.client.post("/auth/demo-login", json={"customer_id": customer_id}).json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}

            orders = self.client.get("/me/orders", headers=headers).json()
            summary = next(item for item in orders if item["order_id"] == order_id)
            self.assertEqual(summary["product"]["product_id"], order["product_id"])

            detail = self.client.get(f"/me/orders/{order_id}", headers=headers).json()
            self.assertEqual(detail["product"]["product_id"], order["product_id"])
            self.assertEqual(detail["tracking"]["order_id"], order_id)
            self.assertTrue(detail["tracking"]["checkpoints"])


if __name__ == "__main__":
    unittest.main()
