"""Customized goods (REFUND-009): a no-reason refund is refused at intake --
no service request is created -- while quality / damage claims on the same
item are still accepted. Runs the real conversation graph with the LLM calls
stubbed, like the evaluation harness."""

import os
import unittest
from datetime import date, timedelta
from unittest.mock import patch
from uuid import uuid4

from src.backend.repositories.mock_db_repository import get_order

CUSTOMER_ID = "CUS0106"
CUSTOMIZED_ORDER = "ORD9901060002"  # generated demo account: engraved necklace PRO0018


def _fixed_date_within_window() -> type:
    delivered = date.fromisoformat(get_order(CUSTOMIZED_ORDER)["delivered_date"])

    class FixedDate(date):
        @classmethod
        def today(cls):
            return delivered + timedelta(days=2)

    return FixedDate


class CustomizedIntakeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.pop("DATABASE_URL", None)
        os.environ["ENABLE_LLM_RESPONSE"] = "false"
        from src.backend.graph.workflow import customer_service_conversation_graph
        cls.graph = customer_service_conversation_graph

    def _run(self, reason: str) -> tuple[dict, list]:
        created = []

        def fake_create(**kwargs):
            created.append(kwargs)
            return ({"request_id": "SR-TEST", "workflow_status": "Pending", **kwargs}, True)

        config = {"configurable": {"thread_id": f"customized-{uuid4().hex}"}}
        with patch("src.backend.agents.conversation_router_agent.chat_json", return_value={"conversation_intent": "AfterSalesRequest", "confirmed": True, "declined": False}), \
             patch("src.backend.agents.request_intent_agent.chat_json", return_value={"request_type": "Refund", "request_reason": reason, "requested_quantity": 1}), \
             patch("src.backend.actions.service_request_action.create_service_request", side_effect=fake_create), \
             patch("src.backend.actions.service_request_action.date", _fixed_date_within_window()):
            context = {"authenticated_customer_id": CUSTOMER_ID, "active_order_id": CUSTOMIZED_ORDER, "order_context_confirmed": False}
            output = {}
            for message in ("我想申请售后", "是的"):
                snapshot = self.graph.get_state(config)
                ctx = dict((snapshot.values or {}).get("conversation_context") or context)
                ctx["authenticated_customer_id"] = CUSTOMER_ID
                output = self.graph.invoke({"user_message": message, "conversation_context": ctx, "conversation_id": "t"}, config=config)
        return output, created

    def test_no_reason_refund_on_customized_goods_creates_no_request(self) -> None:
        output, created = self._run("不想要了")
        self.assertEqual(created, [])
        self.assertIn("定制商品", output["customer_response"])
        self.assertIn("不会为您创建售后申请", output["customer_response"])
        self.assertTrue(output["conversation_context"]["last_eligibility_denial"]["customized"])

    def test_quality_claim_on_customized_goods_is_still_accepted(self) -> None:
        output, created = self._run("质量问题")
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0]["request_reason"], "质量问题")


if __name__ == "__main__":
    unittest.main()
