import json
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.backend.services.ai_review_service import classify, compute_ai_review, evaluation_candidates


def _resolved(request_id, *, human, amount=None, ai=None, reason="不想要了", notes=None, at="2026-11-03T10:00:00+00:00"):
    return {
        "request_id": request_id, "order_id": f"O-{request_id}", "customer_id": "CUS0001",
        "request_reason": reason, "decision": human, "approved_amount": amount,
        "resolved_by": "agent_a", "resolved_at": at, "resolution_notes": notes,
        "ai_recommendation": ai,
    }


def _ai(decision, amount=None, rule="no_reason_return"):
    return {"decision": decision, "approved_amount": amount, "matched_rule_id": rule, "decision_confidence": 0.9}


class ClassifyTests(unittest.TestCase):
    def test_outcomes(self) -> None:
        self.assertEqual(classify(None, "Approve", 10), "no_suggestion")
        self.assertEqual(classify(_ai("Approve", 10.0), "Approve", 10.0), "agree")
        self.assertEqual(classify(_ai("Approve", 10.0), "Approve", 8.0), "amount_differs")
        self.assertEqual(classify(_ai("Approve", 10.0), "PartialApprove", 5.0), "amount_differs")
        self.assertEqual(classify(_ai("Approve", 10.0), "Reject", None), "ai_more_lenient")
        self.assertEqual(classify(_ai("Reject"), "Approve", 10.0), "ai_more_strict")
        self.assertEqual(classify(_ai("Reject"), "NoActionRequired", None), "other")


class ComputeAiReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.requests = [
            _resolved("A", human="Approve", amount=399.0, ai=_ai("Approve", 399.0)),
            _resolved("B", human="Reject", ai=_ai("Approve", 799.0, rule="quality_evidence"), reason="质量问题",
                      notes="图片显示人为损坏", at="2026-11-03T12:00:00+00:00"),
            _resolved("C", human="Approve", amount=50.0, ai=None),
            {"request_id": "D", "decision": "Approve", "resolved_by": None, "ai_recommendation": None},  # automatic
        ]
        self.review = compute_ai_review(self.requests)

    def test_totals_and_rate_only_count_human_resolutions_with_a_suggestion(self) -> None:
        self.assertEqual(self.review["totals"], {
            "human_resolved": 3, "with_suggestion": 2, "without_suggestion": 1, "agree": 1, "disagree": 1,
        })
        self.assertEqual(self.review["agreement_rate"], 0.5)

    def test_breakdowns_put_the_weakest_group_first(self) -> None:
        self.assertEqual(self.review["by_rule"][0], {"rule": "quality_evidence", "total": 1, "agree": 0, "agreement_rate": 0.0})
        self.assertEqual(self.review["by_reason"][0]["reason"], "质量问题")
        self.assertIn({"ai": "Approve", "human": "Reject", "count": 1}, self.review["matrix"])

    def test_disagreement_cases_carry_both_sides(self) -> None:
        [case] = self.review["disagreements"]
        self.assertEqual(case["request_id"], "B")
        self.assertEqual(case["outcome"], "ai_more_lenient")
        self.assertEqual((case["ai_decision"], case["ai_amount"], case["human_decision"]), ("Approve", 799.0, "Reject"))
        self.assertEqual(case["resolution_notes"], "图片显示人为损坏")

    def test_evaluation_candidates_are_only_overrides(self) -> None:
        candidates = evaluation_candidates(self.requests)
        self.assertEqual([c["case_id"] for c in candidates], ["HUMAN_OVERRIDE_B"])
        self.assertEqual(candidates[0]["expected_paths"]["final_result.decision"], "Reject")
        self.assertEqual(candidates[0]["status"], "candidate_needs_fixture")


class ShadowRecommendationGraphTests(unittest.TestCase):
    """End to end through the decision graph on the evaluation fixtures."""

    @classmethod
    def setUpClass(cls) -> None:
        os.environ.pop("DATABASE_URL", None)
        os.environ["ENABLE_LLM_RESPONSE"] = "false"
        from src.backend.graph.workflow import customer_service_graph
        cls.graph = customer_service_graph

    def test_escalated_request_records_what_the_rules_would_decide(self) -> None:
        # SR2026080009: quality claim with usable evidence, escalated only
        # because the customer is a high-frequency refunder.
        result = self.graph.invoke({"request_id": "SR2026080009"})["final_result"]
        self.assertEqual(result["handling_status"], "HumanEscalation")
        self.assertIsNone(result["decision"])
        suggestion = result["ai_recommendation"]
        self.assertEqual(suggestion["decision"], "Approve")
        self.assertEqual(suggestion["matched_rule_id"], "quality_evidence")

    def test_no_suggestion_when_no_rule_matches(self) -> None:
        result = self.graph.invoke({"request_id": "SR2026080001"})["final_result"]
        self.assertEqual(result["handling_status"], "NeedsMoreEvidence")
        self.assertIsNone(result["ai_recommendation"])


STAFF_KEYS = json.dumps({"k": {"staff_id": "agent_v", "roles": ["viewer"]}, "r": {"staff_id": "agent_r", "roles": ["reviewer"]}})


@patch.dict(os.environ, {"STAFF_API_KEYS": STAFF_KEYS}, clear=False)
class AiReviewApiTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ.pop("DATABASE_URL", None)
        from src.backend.api.main import app
        self.client = TestClient(app)

    def test_review_is_readable_by_viewers_but_export_needs_a_reviewer(self) -> None:
        self.assertEqual(self.client.get("/staff/ai-review").status_code, 401)
        self.assertEqual(self.client.get("/staff/ai-review", headers={"Authorization": "Bearer k"}).status_code, 200)
        self.assertEqual(self.client.get("/staff/ai-review/export", headers={"Authorization": "Bearer k"}).status_code, 403)
        export = self.client.get("/staff/ai-review/export", headers={"Authorization": "Bearer r"})
        self.assertEqual(export.status_code, 200)
        self.assertIn("attachment", export.headers["content-disposition"])


if __name__ == "__main__":
    unittest.main()
