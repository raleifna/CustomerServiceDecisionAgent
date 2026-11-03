import json
import os
import threading
import unittest
from urllib.parse import urlparse

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "").strip()
database_name = urlparse(TEST_DATABASE_URL).path.rsplit("/", 1)[-1] if TEST_DATABASE_URL else ""
SAFE_TEST_DATABASE = bool(TEST_DATABASE_URL and "test" in database_name.lower())

STAFF_KEYS = json.dumps({
    "key-a": {"staff_id": "agent_a", "roles": ["reviewer"]},
    "key-b": {"staff_id": "agent_b", "roles": ["reviewer"]},
    "key-v": {"staff_id": "viewer_v", "roles": ["viewer"]},
})


@unittest.skipUnless(SAFE_TEST_DATABASE, "Set TEST_DATABASE_URL to a dedicated database whose name contains 'test'")
class LiveChatIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.saved = {key: os.environ.get(key) for key in ("DATABASE_URL", "STAFF_API_KEYS", "AUTH_SECRET")}
        os.environ.update({"DATABASE_URL": TEST_DATABASE_URL, "STAFF_API_KEYS": STAFF_KEYS, "AUTH_SECRET": "live-test"})
        from src.backend.repositories.postgres_repository import setup_application_schema
        setup_application_schema(TEST_DATABASE_URL)

    @classmethod
    def tearDownClass(cls) -> None:
        for key, value in cls.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def setUp(self) -> None:
        import psycopg
        with psycopg.connect(TEST_DATABASE_URL) as connection, connection.cursor() as cursor:
            cursor.execute("TRUNCATE live_messages, human_handoffs RESTART IDENTITY CASCADE")

    # ---------- repository ----------

    def test_customer_is_never_queued_twice(self) -> None:
        from src.backend.repositories import live_chat_repository as live
        first = live.create_or_reuse_handoff(customer_id="CUS0001", reason="要人工")
        second = live.create_or_reuse_handoff(customer_id="CUS0001", reason="再要一次")
        self.assertEqual(first["handoff_id"], second["handoff_id"])
        self.assertEqual(first["workflow_status"], "Waiting")
        self.assertEqual([m["sender"] for m in live.list_messages(first["handoff_id"])], ["system"])

    def test_concurrent_handoff_requests_create_one_session(self) -> None:
        from src.backend.repositories import live_chat_repository as live
        barrier, ids = threading.Barrier(4), []

        def request() -> None:
            barrier.wait()
            ids.append(live.create_or_reuse_handoff(customer_id="CUS0002", reason="并发")["handoff_id"])

        threads = [threading.Thread(target=request) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(set(ids)), 1)

    def test_lifecycle_accept_message_end(self) -> None:
        from src.backend.repositories import live_chat_repository as live
        handoff = live.create_or_reuse_handoff(customer_id="CUS0001", reason="要人工")
        hid = handoff["handoff_id"]
        live.add_message(hid, sender="customer", sender_id="CUS0001", content="排队时先说一句")
        self.assertEqual(live.accept_handoff(hid, "agent_a")["workflow_status"], "Active")
        self.assertEqual(live.accept_handoff(hid, "agent_a")["staff_id"], "agent_a")  # idempotent for the owner
        with self.assertRaises(live.LiveChatConflict):
            live.accept_handoff(hid, "agent_b")
        live.add_message(hid, sender="staff", sender_id="agent_a", content="您好")

        changes = live.changes_since(after_message_id=0, since_handoff_update=None, customer_id="CUS0001")
        self.assertEqual([m["sender"] for m in changes["messages"]], ["system", "customer", "system", "staff"])
        self.assertEqual(live.changes_since(after_message_id=0, since_handoff_update=None, customer_id="CUS0009")["messages"], [])

        live.end_handoff(hid, ended_by="staff", reason="done")
        self.assertIsNone(live.open_handoff_for_customer("CUS0001"))
        with self.assertRaises(live.LiveChatConflict):
            live.add_message(hid, sender="customer", sender_id="CUS0001", content="还在吗")
        with self.assertRaises(live.LiveChatConflict):
            live.end_handoff(hid, ended_by="customer", reason="again")
        # A new request after the end opens a fresh session.
        self.assertNotEqual(live.create_or_reuse_handoff(customer_id="CUS0001", reason="又来")["handoff_id"], hid)

    # ---------- API ----------

    def _client(self):
        from fastapi.testclient import TestClient
        from src.backend.api.main import app
        return TestClient(app)

    def _customer(self, customer_id: str) -> dict:
        from src.backend.api.auth_tokens import issue_token
        token, _ = issue_token("customer", customer_id, ttl_seconds=600)
        return {"Authorization": f"Bearer {token}"}

    def test_customer_and_staff_converse_through_the_api(self) -> None:
        from src.backend.repositories import live_chat_repository as live
        client = self._client()
        customer = self._customer("CUS0001")
        staff_a, staff_b, viewer = ({"Authorization": f"Bearer {key}"} for key in ("key-a", "key-b", "key-v"))

        self.assertEqual(client.get("/me/live", headers=customer).json(), {"handoff": None, "messages": []})
        self.assertEqual(client.post("/me/live/messages", headers=customer, json={"content": "hi"}).status_code, 404)

        hid = live.create_or_reuse_handoff(customer_id="CUS0001", reason="要人工")["handoff_id"]
        self.assertEqual(client.post("/me/live/messages", headers=customer, json={"content": "在吗"}).status_code, 201)

        listed = client.get("/staff/live", headers=viewer).json()
        self.assertEqual([(h["handoff_id"], h["workflow_status"], h["last_message"]) for h in listed], [(hid, "Waiting", "在吗")])
        self.assertEqual(client.post(f"/staff/live/{hid}/accept", headers=viewer).status_code, 403)
        # Must accept before replying.
        self.assertEqual(client.post(f"/staff/live/{hid}/messages", headers=staff_a, json={"content": "您好"}).status_code, 409)
        self.assertEqual(client.post(f"/staff/live/{hid}/accept", headers=staff_a).status_code, 200)
        self.assertEqual(client.post(f"/staff/live/{hid}/accept", headers=staff_b).status_code, 409)
        self.assertEqual(client.post(f"/staff/live/{hid}/messages", headers=staff_b, json={"content": "抢答"}).status_code, 409)
        self.assertEqual(client.post(f"/staff/live/{hid}/messages", headers=staff_a, json={"content": "您好"}).status_code, 201)

        mine = client.get("/me/live", headers=customer).json()
        self.assertEqual(mine["handoff"]["staff_id"], "agent_a")
        self.assertEqual([m["content"] for m in mine["messages"] if m["sender"] != "system"], ["在吗", "您好"])
        # Another customer sees nothing of it.
        self.assertIsNone(client.get("/me/live", headers=self._customer("CUS0002")).json()["handoff"])

        detail = client.get(f"/staff/live/{hid}", headers=viewer).json()
        self.assertEqual(detail["handoff"]["handoff_id"], hid)
        self.assertEqual(detail["ai_transcript"], [])

        self.assertEqual(client.post("/me/live/end", headers=customer).status_code, 200)
        self.assertEqual(client.post(f"/staff/live/{hid}/messages", headers=staff_a, json={"content": "?"}).status_code, 409)

    def test_staff_can_start_a_conversation_from_a_case(self) -> None:
        import psycopg
        with psycopg.connect(TEST_DATABASE_URL) as connection, connection.cursor() as cursor:
            cursor.execute("TRUNCATE service_requests RESTART IDENTITY CASCADE")
            cursor.execute(
                "INSERT INTO service_requests (request_id, order_id, customer_id, request_type, request_reason, "
                "requested_quantity, request_date, workflow_status) VALUES "
                "('SRL-LIVE-1', 'ORD2026080001', 'CUS0001', 'Refund', '质量问题', 1, '2026-11-01', 'EscalatedToHuman')"
            )
        client = self._client()
        response = client.post("/staff/live/start-from-request/SRL-LIVE-1", headers={"Authorization": "Bearer key-a"},
                               json={"message": "您好，想跟您确认一下商品问题"})
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual((body["workflow_status"], body["staff_id"], body["request_id"]), ("Active", "agent_a", "SRL-LIVE-1"))
        mine = client.get("/me/live", headers=self._customer("CUS0001")).json()
        self.assertEqual(mine["messages"][-1]["content"], "您好，想跟您确认一下商品问题")
        # Another agent cannot hijack the customer's active conversation.
        other = client.post("/staff/live/start-from-request/SRL-LIVE-1", headers={"Authorization": "Bearer key-b"}, json={})
        self.assertEqual(other.status_code, 409)


if __name__ == "__main__":
    unittest.main()
