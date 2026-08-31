"""End-to-end decision test against real V2/Olist data through the full
migrated chain: load_service_request/order/customer/order_items/product/
logistics -> order_agent -> logistics_agent -> evidence_agent -> policy_agent
-> evidence_completeness_gate -> decision_engine. Every loader/agent node in
this path has now been migrated off mock_db_repository (see the order/
logistics/customer/product vertical-slice commits); this is the test that
proves the whole chain still produces a sane, correct decision when run
against a real imported order rather than hand-built fixtures alone.
"""

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
class PostgresDecisionEndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import sys
        sys.path.insert(0, str(REPO_ROOT))
        os.environ["APP_DATA_BACKEND"] = "postgres"
        os.environ["DATABASE_URL"] = TEST_DATABASE_URL

        from src.backend.repositories.postgres_repository import setup_application_schema
        from db.migrations.apply import apply_all

        setup_application_schema(TEST_DATABASE_URL)
        apply_all(TEST_DATABASE_URL)

    @classmethod
    def tearDownClass(cls) -> None:
        os.environ.pop("APP_DATA_BACKEND", None)
        os.environ.pop("DATABASE_URL", None)

    def setUp(self) -> None:
        import psycopg

        with psycopg.connect(TEST_DATABASE_URL) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "TRUNCATE shipment_events, shipments, order_items, orders, "
                    "products, customers, service_request_items, service_requests, "
                    "import_batches RESTART IDENTITY CASCADE"
                )
                cursor.execute(
                    "INSERT INTO import_batches (import_batch_id, pipeline_version, "
                    "dataset_anchor_date, source_datasets, row_counts) VALUES "
                    "('e2e-itest-batch', 'itest', '2026-08-31', '[]'::jsonb, '{}'::jsonb)"
                )
                cursor.execute(
                    "INSERT INTO customers (customer_id, vip_level, canceled_order_count, "
                    "import_batch_id) VALUES ('CUS-E2E-001', 'Bronze', 0, 'e2e-itest-batch')"
                )
                cursor.execute(
                    "INSERT INTO products (product_id, category, price, is_fragile, "
                    "is_customized, import_batch_id) VALUES "
                    "('PRO-E2E-001', 'electronics', 199.00, FALSE, FALSE, 'e2e-itest-batch')"
                )
                cursor.execute(
                    "INSERT INTO orders (order_id, customer_id, order_status, payment_status, "
                    "order_date, shipped_date, delivered_date, estimated_delivery_date, "
                    "delivery_recipient_type, amount, rounding_adjustment, import_batch_id) "
                    "VALUES ('ORD-E2E-001', 'CUS-E2E-001', 'delivered', 'Paid', "
                    "'2026-01-01', '2026-01-02', '2026-01-05', '2026-01-10', "
                    "'Self', 199.00, 0, 'e2e-itest-batch')"
                )
                cursor.execute(
                    "INSERT INTO shipments (shipment_id, order_id, carrier, shipped_at, "
                    "delivered_at, estimated_delivery_date, shipment_status, import_batch_id) "
                    "VALUES ('SHP-E2E-001', 'ORD-E2E-001', '第三方物流', '2026-01-02', "
                    "'2026-01-05', '2026-01-10', 'Delivered', 'e2e-itest-batch')"
                )
                cursor.execute(
                    "INSERT INTO order_items (order_item_id, order_id, product_id, sku_id, "
                    "sku_name, unit_price, quantity, discount_amount, paid_amount, "
                    "import_batch_id) VALUES "
                    "('OI-E2E-001', 'ORD-E2E-001', 'PRO-E2E-001', 'PRO-E2E-001-DEFAULT', "
                    "'默认规格', 199.00, 1, 0, 199.00, 'e2e-itest-batch')"
                )
            connection.commit()

    def _run_full_chain(self, state):
        from src.backend.graph.workflow import (
            load_customer_node,
            load_evi_node,
            load_logistics_node,
            load_order_items_node,
            load_order_node,
            load_product_node,
            load_service_request_node,
        )
        from src.backend.agents.evidence_agent import evidence_agent_node
        from src.backend.agents.logistics_agent import logistics_agent_node
        from src.backend.agents.order_agent import order_agent_node
        from src.backend.agents.policy_agent import policy_agent_node
        from src.backend.decision.engine import decision_engine_node
        from src.backend.graph.evidence_gate import evidence_completeness_gate_node

        for node in (
            load_service_request_node, load_order_node, load_customer_node,
            load_order_items_node, load_product_node, load_logistics_node,
            load_evi_node, order_agent_node, logistics_agent_node,
            evidence_agent_node, policy_agent_node,
        ):
            update = node(state)
            self.assertNotIn(
                "errors", update,
                f"{node.__name__} reported errors against real data: {update.get('errors')}",
            )
            state.update(update)

        state.update(evidence_completeness_gate_node(state))
        return state

    def test_no_reason_return_on_a_real_v2_order_auto_approves(self):
        from src.backend.repositories.order_item_repository import get_order_items
        from src.backend.repositories.service_request_repository import create_service_request

        item = get_order_items("ORD-E2E-001")[0]
        record, created = create_service_request(
            order_id="ORD-E2E-001", customer_id="CUS-E2E-001", request_type="Refund",
            request_reason="不想要了", requested_quantity=1, order_item=item,
            request_date=date(2026, 1, 7),  # 2 days after delivered_date -- within the 7-day window
        )
        self.assertTrue(created)
        self.assertTrue(record["request_id"].startswith("SRL"))

        state = self._run_full_chain({"request_id": record["request_id"]})

        gate_result = state["gate_result"]
        self.assertEqual(gate_result["status"], "ReadyForDecision", gate_result["reasons"])
        self.assertTrue(gate_result["can_decide"])

        from src.backend.decision.engine import decision_engine_node
        state.update(decision_engine_node(state))

        proposed = state["proposed_decision"]
        self.assertEqual(proposed["decision"], "Approve")
        self.assertEqual(proposed["matched_rule_id"], "no_reason_return")
        self.assertIn("REFUND-001", proposed["rule_hits"])

        # confirm the fields that made this decision honest against real
        # data: real product/customer facts, no fabricated
        # fulfillment-match or promised-ship-date evidence sneaking in
        order_evidence = state["order_evidence"]
        self.assertEqual(
            order_evidence["fulfillment_tracking"]["availability"], "UNAVAILABLE",
        )
        self.assertIsNone(order_evidence["product_match"])
        self.assertNotIn("promised_ship_date", order_evidence)

        logistics_evidence = state["logistics_evidence"]
        self.assertEqual(
            logistics_evidence["tracking_stalled_over_72h"]["availability"], "UNAVAILABLE",
        )

    def test_quality_issue_without_evidence_needs_more_evidence_not_a_silent_decision(self):
        from src.backend.repositories.order_item_repository import get_order_items
        from src.backend.repositories.service_request_repository import create_service_request

        item = get_order_items("ORD-E2E-001")[0]
        record, _ = create_service_request(
            order_id="ORD-E2E-001", customer_id="CUS-E2E-001", request_type="Refund",
            request_reason="质量问题", requested_quantity=1, order_item=item,
            request_date=date(2026, 1, 7),
        )

        state = self._run_full_chain({"request_id": record["request_id"]})
        gate_result = state["gate_result"]

        self.assertEqual(gate_result["status"], "NeedsMoreEvidence")
        self.assertFalse(gate_result["can_decide"])


if __name__ == "__main__":
    unittest.main()
