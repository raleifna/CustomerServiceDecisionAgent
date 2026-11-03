"""Read endpoints added for the staff workbench: case history, return-order
queue and the enriched request detail. Runs in fixture mode (no
DATABASE_URL), where history falls back to the JSON seed requests and the
Postgres-only return-order queue is empty."""

import json
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.backend.api.main import app
from src.backend.repositories.mock_db_repository import load_table

STAFF_KEYS = json.dumps({"viewer-token": {"staff_id": "agent_v", "roles": ["viewer"]}})
AUTH = {"Authorization": "Bearer viewer-token"}


@patch.dict(os.environ, {"STAFF_API_KEYS": STAFF_KEYS}, clear=False)
class StaffWorkbenchApiTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ.pop("DATABASE_URL", None)
        self.client = TestClient(app)

    def test_history_requires_staff_auth(self) -> None:
        self.assertEqual(self.client.get("/staff/service-requests").status_code, 401)
        self.assertEqual(self.client.get("/staff/return-orders").status_code, 401)

    def test_history_lists_seed_requests_with_product_cards(self) -> None:
        response = self.client.get("/staff/service-requests", headers=AUTH)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body), len(load_table("service_requests.json")))
        self.assertTrue(all(item["product"] for item in body))
        dates = [item["request_date"] for item in body]
        self.assertEqual(dates, sorted(dates, reverse=True))

    def test_history_filters_by_status_and_query(self) -> None:
        seed = load_table("service_requests.json")
        target = seed[0]
        by_query = self.client.get("/staff/service-requests", params={"q": target["order_id"].lower()}, headers=AUTH).json()
        self.assertIn(target["request_id"], [item["request_id"] for item in by_query])
        self.assertTrue(all(target["order_id"] == item["order_id"] for item in by_query))

        status = target["workflow_status"]
        by_status = self.client.get("/staff/service-requests", params={"workflow_status": status}, headers=AUTH).json()
        self.assertTrue(by_status)
        self.assertTrue(all(item["workflow_status"] == status for item in by_status))

    def test_return_order_queue_is_empty_without_database(self) -> None:
        response = self.client.get("/staff/return-orders", headers=AUTH)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])
        self.assertEqual(self.client.get("/staff/return-orders", params={"scope": "bogus"}, headers=AUTH).status_code, 422)

    def test_detail_carries_product_tracking_and_customer_history(self) -> None:
        seed = load_table("service_requests.json")[0]
        response = self.client.get(f"/staff/service-requests/{seed['request_id']}", headers=AUTH)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["service_request"]["product"]["product_id"], body["order"]["product_id"])
        self.assertEqual(body["service_request"]["order_amount"], float(body["order"]["amount"]))
        self.assertIn("tracking", body)
        self.assertNotIn(seed["request_id"], [item["request_id"] for item in body["customer_requests"]])


if __name__ == "__main__":
    unittest.main()
