import json
import tempfile
import unittest
from pathlib import Path

from src.backend.config.business_rules import (
    BusinessRulesError,
    load_business_rules,
)
from src.backend.graph.evidence_gate import evidence_completeness_gate_node
from src.backend.decision.rule_engine import (
    DecisionRulesError,
    evaluate_decision,
    automatic_policy_ids,
    load_decision_rules,
)
from src.backend.actions.service_request_action import _special_issue_guidance


class BusinessRulesTests(unittest.TestCase):
    def tearDown(self) -> None:
        load_business_rules.cache_clear()

    def test_rules_can_be_loaded_from_an_environment_specific_file(self) -> None:
        rules = load_business_rules()
        custom = json.loads(json.dumps(rules))
        custom["risk"]["high_value_amount"] = 8000

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rules.json"
            path.write_text(json.dumps(custom), encoding="utf-8")
            loaded = load_business_rules(str(path))

        self.assertEqual(loaded["risk"]["high_value_amount"], 8000)

    def test_invalid_threshold_fails_fast(self) -> None:
        rules = load_business_rules()
        invalid = json.loads(json.dumps(rules))
        invalid["refund"]["no_reason_return_days"] = -1

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rules.json"
            path.write_text(json.dumps(invalid), encoding="utf-8")
            with self.assertRaises(BusinessRulesError):
                load_business_rules(str(path))


class EvidenceGateTests(unittest.TestCase):
    def test_logistics_event_does_not_prove_product_damage(self) -> None:
        state = {
            "service_request": {"request_reason": "商品损坏"},
            "order_evidence": {},
            "logistics_evidence": {"has_logistics_exception": True},
            "evidence_review": {
                "evidence_available": False,
                "has_usable_evidence": False,
            },
            "policy_evidence": {
                "candidate_policy_ids": ["REFUND-008"],
                "missing_policy_ids": [],
            },
            "errors": [],
        }

        result = evidence_completeness_gate_node(state)

        self.assertEqual(result["gate_result"]["status"], "NeedsMoreEvidence")
        self.assertFalse(result["gate_result"]["can_decide"])

    def test_special_issue_guidance_matches_product_category(self) -> None:
        self.assertIn("功能故障", _special_issue_guidance("数码"))
        self.assertNotIn("尺码", _special_issue_guidance("数码"))
        self.assertIn("尺码", _special_issue_guidance("服饰"))

    def test_uncovered_business_case_routes_to_reflection(self) -> None:
        state = {
            "service_request": {"request_reason": "质量问题"},
            "order_evidence": {},
            "logistics_evidence": {},
            "evidence_review": {"has_usable_evidence": True},
            "policy_evidence": {
                "candidate_policy_ids": ["VIP-001"],
                "missing_policy_ids": [],
            },
            "errors": [],
        }

        result = evidence_completeness_gate_node(state)

        self.assertEqual(result["gate_result"]["status"], "NeedsReflection")
        self.assertEqual(result["gate_result"]["route"], "reflection")


class DeclarativeDecisionRuleTests(unittest.TestCase):
    def tearDown(self) -> None:
        load_decision_rules.cache_clear()

    def test_rule_registry_is_priority_sorted(self) -> None:
        rules = load_decision_rules()
        priorities = [rule["priority"] for rule in rules]
        self.assertEqual(priorities, sorted(priorities, reverse=True))

    def test_gate_policy_set_is_derived_from_rule_registry(self) -> None:
        self.assertIn("REFUND-010", automatic_policy_ids())
        self.assertNotIn("SHIPPING-002", automatic_policy_ids())

    def test_partial_approval_is_computed_from_configured_expression(self) -> None:
        result = evaluate_decision(
            {
                "service_request": {
                    "request_reason": "少发商品",
                    "request_type": "PartialRefund",
                    "requested_quantity": 2,
                },
                "order_evidence": {"confirmed_missing_quantity": 1},
                "logistics_evidence": {},
                "evidence_review": {},
                "product": {"price": 79},
                "policy_ids": {"REFUND-005", "RISK-006"},
            }
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["matched_rule_id"], "missing_product")
        self.assertEqual(result["decision"], "PartialApprove")
        self.assertEqual(result["approved_quantity"], 1)
        self.assertEqual(result["approved_amount"], 79.0)
        self.assertEqual(result["rule_hits"], ["REFUND-005", "RISK-006"])

    def test_unknown_condition_operator_fails_at_load_time(self) -> None:
        invalid = {
            "rules": [{
                "id": "bad",
                "priority": 1,
                "when": [{"path": "x", "op": "execute_python", "value": True}],
                "decision": "Approve",
            }]
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rules.json"
            path.write_text(json.dumps(invalid), encoding="utf-8")
            with self.assertRaises(DecisionRulesError):
                load_decision_rules(str(path))


if __name__ == "__main__":
    unittest.main()
