"""Customer and staff authentication: identity always comes from a signed
token, never from a customer_id the caller supplies."""

import json
import os
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.backend.api import auth_tokens, main
from src.backend.api.auth_tokens import issue_token, sign_file_path, verify_token
from src.backend.repositories.mock_db_repository import load_table

ENV = {
    "AUTH_SECRET": "unit-test-secret",
    "DEMO_LOGIN_ENABLED": "true",
    "STAFF_API_KEYS": json.dumps({"reviewer-key": {"staff_id": "agent_r", "roles": ["reviewer"]}}),
}


def _orders_by_customer() -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for order in load_table("orders.json"):
        grouped.setdefault(order["customer_id"], []).append(order)
    return grouped


@patch.dict(os.environ, ENV, clear=False)
class TokenTests(unittest.TestCase):
    def test_round_trip_and_kind_separation(self) -> None:
        token, _ = issue_token("customer", "CUS0001", ttl_seconds=60)
        self.assertEqual(verify_token(token, kind="customer")["sub"], "CUS0001")
        self.assertIsNone(verify_token(token, kind="staff"))

    def test_tampered_and_expired_tokens_are_rejected(self) -> None:
        token, _ = issue_token("customer", "CUS0001", ttl_seconds=60)
        body, signature = token.split(".")
        forged_body = body[:-2] + ("AA" if body[-2:] != "AA" else "BB")
        self.assertIsNone(verify_token(f"{forged_body}.{signature}", kind="customer"))
        self.assertIsNone(verify_token("not-a-token", kind="customer"))
        expired, _ = issue_token("customer", "CUS0001", ttl_seconds=-1)
        self.assertIsNone(verify_token(expired, kind="customer"))

    def test_tokens_from_another_secret_are_rejected(self) -> None:
        token, _ = issue_token("customer", "CUS0001", ttl_seconds=60)
        with patch.dict(os.environ, {"AUTH_SECRET": "a-different-secret"}):
            self.assertIsNone(verify_token(token, kind="customer"))


@patch.dict(os.environ, ENV, clear=False)
class CustomerAuthApiTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ.pop("DATABASE_URL", None)
        self.client = TestClient(main.app)
        grouped = _orders_by_customer()
        self.customer_a, self.customer_b = sorted(grouped)[:2]
        self.order_b = grouped[self.customer_b][0]["order_id"]

    def _login(self, customer_id: str) -> dict:
        response = self.client.post("/auth/demo-login", json={"customer_id": customer_id})
        self.assertEqual(response.status_code, 200, response.text)
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    def test_demo_login_can_be_disabled(self) -> None:
        with patch.dict(os.environ, {"DEMO_LOGIN_ENABLED": "false"}):
            self.assertEqual(self.client.post("/auth/demo-login", json={"customer_id": self.customer_a}).status_code, 404)
            self.assertEqual(self.client.get("/auth/demo-accounts").status_code, 404)

    def test_unknown_account_cannot_log_in(self) -> None:
        self.assertEqual(self.client.post("/auth/demo-login", json={"customer_id": "CUS9999"}).status_code, 404)

    def test_customer_endpoints_require_a_token(self) -> None:
        for path in ("/me/orders", "/me/service-requests", f"/me/orders/{self.order_b}", "/auth/me"):
            self.assertEqual(self.client.get(path).status_code, 401, path)
        self.assertEqual(self.client.post("/chat", json={"conversation_id": "c1", "message": "hi"}).status_code, 401)

    def test_me_endpoints_are_scoped_to_the_token(self) -> None:
        headers = self._login(self.customer_a)
        self.assertEqual(self.client.get("/auth/me", headers=headers).json()["customer_id"], self.customer_a)
        orders = self.client.get("/me/orders", headers=headers).json()
        self.assertTrue(orders)
        self.assertTrue(all(order["customer_id"] == self.customer_a for order in orders))
        # Another customer's order is refused, even with a valid login.
        self.assertEqual(self.client.get(f"/me/orders/{self.order_b}", headers=headers).status_code, 403)

    def test_requests_of_another_customer_are_refused(self) -> None:
        seed = next(r for r in load_table("service_requests.json") if r["customer_id"] != self.customer_a)
        headers = self._login(self.customer_a)
        self.assertEqual(self.client.get(f"/service-requests/{seed['request_id']}/status", headers=headers).status_code, 403)
        self.assertEqual(self.client.post(f"/service-requests/{seed['request_id']}/withdraw", headers=headers).status_code, 403)
        self.assertEqual(
            self.client.post(f"/service-requests/{seed['request_id']}/return-shipment", headers=headers,
                             json={"carrier": "顺丰速运", "tracking_number": "SF1234567890"}).status_code,
            403,
        )

    def test_chat_ignores_a_customer_id_in_the_body(self) -> None:
        headers = self._login(self.customer_a)
        response = self.client.post(
            "/chat", headers=headers,
            json={"conversation_id": "c1", "message": "hi", "order_id": self.order_b, "customer_id": self.customer_b},
        )
        self.assertEqual(response.status_code, 403)

    def test_staff_token_is_not_a_customer_token(self) -> None:
        staff_token, _ = issue_token("staff", self.customer_a, ttl_seconds=60)
        self.assertEqual(self.client.get("/me/orders", headers={"Authorization": f"Bearer {staff_token}"}).status_code, 401)


@patch.dict(os.environ, ENV, clear=False)
class SignedUploadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(main.app)
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        (self.root / "CUS0001").mkdir()
        (self.root / "CUS0001" / "photo.png").write_bytes(b"\x89PNG fake")
        self.patcher = patch.object(main, "_UPLOADS_DIR", self.root)
        self.patcher.start()

    def tearDown(self) -> None:
        self.patcher.stop()
        self.tmp.cleanup()

    def test_signed_url_serves_the_file(self) -> None:
        url = auth_tokens.signed_upload_url("data/uploads/CUS0001/photo.png")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"\x89PNG fake")

    def test_unsigned_tampered_or_expired_urls_are_refused(self) -> None:
        self.assertEqual(self.client.get("/uploads/CUS0001/photo.png").status_code, 403)
        signature = sign_file_path("CUS0001/photo.png")
        self.assertEqual(self.client.get(f"/uploads/CUS0001/other.png?{signature}").status_code, 403)
        expired = f"exp={int(time.time()) - 5}&sig=" + sign_file_path("CUS0001/photo.png").split("sig=")[1]
        self.assertEqual(self.client.get(f"/uploads/CUS0001/photo.png?{expired}").status_code, 403)

    def test_path_traversal_is_refused_even_when_signed(self) -> None:
        escaping = "../outside.txt"
        (self.root.parent / "outside.txt").write_text("secret")
        try:
            response = self.client.get(f"/uploads/{escaping}?{sign_file_path(escaping)}")
            self.assertIn(response.status_code, {403, 404})
        finally:
            (self.root.parent / "outside.txt").unlink()


@patch.dict(os.environ, ENV, clear=False)
class StaffSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ.pop("DATABASE_URL", None)
        self.client = TestClient(main.app)

    def test_access_key_exchanges_for_a_session_token(self) -> None:
        response = self.client.post("/staff/auth/login", json={"access_key": "reviewer-key"})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["staff_id"], "agent_r")
        session = {"Authorization": f"Bearer {body['access_token']}"}
        self.assertEqual(self.client.get("/staff/me", headers=session).json()["staff_id"], "agent_r")

    def test_wrong_key_is_refused(self) -> None:
        self.assertEqual(self.client.post("/staff/auth/login", json={"access_key": "nope"}).status_code, 401)

    def test_raw_key_still_works_for_automation(self) -> None:
        self.assertEqual(self.client.get("/staff/me", headers={"Authorization": "Bearer reviewer-key"}).status_code, 200)

    def test_session_of_a_removed_staff_member_stops_working(self) -> None:
        token = self.client.post("/staff/auth/login", json={"access_key": "reviewer-key"}).json()["access_token"]
        with patch.dict(os.environ, {"STAFF_API_KEYS": json.dumps({"other": {"staff_id": "someone_else", "roles": ["reviewer"]}})}):
            self.assertEqual(self.client.get("/staff/me", headers={"Authorization": f"Bearer {token}"}).status_code, 401)

    def test_customer_token_is_not_a_staff_token(self) -> None:
        customer_token, _ = issue_token("customer", "agent_r", ttl_seconds=60)
        self.assertEqual(self.client.get("/staff/me", headers={"Authorization": f"Bearer {customer_token}"}).status_code, 401)


if __name__ == "__main__":
    unittest.main()
