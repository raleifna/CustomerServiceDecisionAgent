import json
import os
import unittest
from datetime import date
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.backend.api.main import app
from src.backend.services.staff_stats_service import compute_staff_stats

TODAY = date(2026, 11, 3)


def _request(request_id, *, status="Closed", decision=None, resolved_by=None, reason="不想要了",
             created="2026-11-02T10:00:00+00:00", resolved=None, amount=None):
    return {
        "request_id": request_id, "workflow_status": status, "decision": decision,
        "resolved_by": resolved_by, "request_reason": reason, "created_at": created,
        "resolved_at": resolved, "approved_amount": amount,
    }


class ComputeStaffStatsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.requests = [
            _request("A", decision="Approve", amount=100.0),
            _request("B", decision="Reject", reason="质量问题"),
            _request("C", status="ReturnAwaitingShipment", decision="Approve", resolved_by="agent_a", amount=50.0,
                     created="2026-11-01T10:00:00+00:00", resolved="2026-11-01T14:00:00+00:00"),
            _request("D", status="EscalatedToHuman", reason="质量问题", created="2026-11-03T09:00:00+00:00"),
            _request("E", status="Pending", created="2026-10-01T09:00:00+00:00"),
        ]
        self.stats = compute_staff_stats(
            self.requests, return_orders=[{"workflow_status": "AwaitingShipment"}],
            action_status_counts={"Succeeded": 2, "Failed": 1}, today=TODAY, days=14,
        )

    def test_totals_and_rates(self) -> None:
        totals, rates = self.stats["totals"], self.stats["rates"]
        self.assertEqual(totals["requests"], 5)
        self.assertEqual(totals["decided"], 3)
        self.assertEqual(totals["decided_by_system"], 2)
        self.assertEqual(totals["decided_by_human"], 1)
        self.assertEqual(totals["awaiting_human"], 1)
        self.assertEqual(totals["open"], 3)
        self.assertEqual(rates["automation_rate"], round(2 / 3, 4))
        # D (still queued) + C (resolved by a human) out of 5.
        self.assertEqual(rates["escalation_rate"], 0.4)
        self.assertEqual(rates["approval_rate"], round(2 / 3, 4))
        self.assertEqual(self.stats["approved_amount_total"], 150.0)
        self.assertEqual(self.stats["avg_human_handling_hours"], 4.0)

    def test_daily_trend_covers_the_window_only(self) -> None:
        daily = self.stats["daily"]
        self.assertEqual(len(daily), 14)
        self.assertEqual(daily[-1]["date"], "2026-11-03")
        by_day = {row["date"]: row for row in daily}
        self.assertEqual(by_day["2026-11-02"], {"date": "2026-11-02", "created": 2, "auto": 2, "human": 0})
        self.assertEqual(by_day["2026-11-01"]["human"], 1)
        self.assertEqual(sum(row["created"] for row in daily), 4)  # E is outside the window

    def test_breakdowns(self) -> None:
        self.assertEqual(self.stats["reasons"][0], {"reason": "不想要了", "count": 3})
        self.assertEqual(self.stats["returns"], [{"status": "AwaitingShipment", "count": 1}])
        self.assertEqual(self.stats["actions"], [{"status": "Failed", "count": 1}, {"status": "Succeeded", "count": 2}])

    def test_empty_input_has_no_rates(self) -> None:
        stats = compute_staff_stats([], return_orders=[], action_status_counts={}, today=TODAY)
        self.assertIsNone(stats["rates"]["automation_rate"])
        self.assertIsNone(stats["avg_human_handling_hours"])


KEYS = json.dumps({
    "viewer-token": {"staff_id": "agent_v", "roles": ["viewer"]},
    "admin-token": {"staff_id": "agent_admin", "roles": ["admin"]},
})


@patch.dict(os.environ, {"STAFF_API_KEYS": KEYS}, clear=False)
class StaffStatsApiTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ.pop("DATABASE_URL", None)
        self.client = TestClient(app)

    def test_requires_auth(self) -> None:
        self.assertEqual(self.client.get("/staff/stats").status_code, 401)

    def test_llm_health_is_admin_only(self) -> None:
        viewer = self.client.get("/staff/stats", headers={"Authorization": "Bearer viewer-token"})
        admin = self.client.get("/staff/stats", headers={"Authorization": "Bearer admin-token"})
        self.assertEqual(viewer.status_code, 200)
        self.assertIsNone(viewer.json()["llm"])
        self.assertIn("total_calls", admin.json()["llm"])
        self.assertGreater(viewer.json()["totals"]["requests"], 0)

    def test_window_is_bounded(self) -> None:
        response = self.client.get("/staff/stats", params={"days": 3}, headers={"Authorization": "Bearer viewer-token"})
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
