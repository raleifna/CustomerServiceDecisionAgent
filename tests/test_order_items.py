import unittest

from src.backend.decision.rule_engine import evaluate_decision
from src.backend.repositories.mock_db_repository import load_table


class OrderItemMigrationTests(unittest.TestCase):
    def test_every_seed_order_has_balanced_item_snapshots(self) -> None:
        orders = {item["order_id"]: item for item in load_table("orders.json")}
        grouped: dict[str, list[dict]] = {}
        for item in load_table("order_items.json"):
            grouped.setdefault(item["order_id"], []).append(item)

        self.assertEqual(set(grouped), set(orders))
        for order_id, items in grouped.items():
            paid_total = round(sum(float(item["paid_amount"]) for item in items), 2)
            self.assertEqual(paid_total, round(float(orders[order_id]["amount"]), 2))

    def test_every_seed_request_targets_one_valid_order_item(self) -> None:
        requests = {item["request_id"]: item for item in load_table("service_requests.json")}
        order_items = {item["order_item_id"]: item for item in load_table("order_items.json")}
        grouped: dict[str, list[dict]] = {}
        for item in load_table("service_request_items.json"):
            grouped.setdefault(item["request_id"], []).append(item)

        self.assertEqual(set(grouped), set(requests))
        for request_id, relations in grouped.items():
            self.assertEqual(len(relations), 1)
            relation = relations[0]
            order_item = order_items[relation["order_item_id"]]
            self.assertEqual(order_item["order_id"], requests[request_id]["order_id"])
            self.assertLessEqual(relation["requested_quantity"], order_item["quantity"])

    def test_refund_uses_purchase_snapshot_not_current_catalog_price(self) -> None:
        decision = evaluate_decision({
            "service_request": {
                "request_reason": "不想要了", "request_type": "Refund",
                "requested_quantity": 1,
            },
            "order_evidence": {}, "logistics_evidence": {}, "evidence_review": {},
            "order_item": {"unit_price": 88.0},
            "product": {"price": 999.0},
            "policy_ids": {"REFUND-001"},
        })
        self.assertEqual(decision["approved_amount"], 88.0)


if __name__ == "__main__":
    unittest.main()
