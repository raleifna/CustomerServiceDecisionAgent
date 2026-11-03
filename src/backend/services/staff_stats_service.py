"""Aggregates for the staff workbench dashboard.

A pure function over already-loaded rows (service requests, return orders,
action-task status counts) so the numbers are unit-testable without a
database. The project's request volume is demo-sized; aggregating in Python
over one query keeps the SQL surface small.
"""

from collections import Counter
from datetime import date, datetime, timedelta
from typing import Any

HUMAN_QUEUE_STATUSES = {"EscalatedToHuman", "NeedsReflection"}
APPROVED_DECISIONS = {"Approve", "PartialApprove"}


def _day(record: dict[str, Any]) -> date | None:
    raw = record.get("created_at") or record.get("request_date")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _parse(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def compute_staff_stats(
    requests: list[dict[str, Any]],
    *,
    return_orders: list[dict[str, Any]],
    action_status_counts: dict[str, int],
    today: date,
    days: int = 14,
) -> dict[str, Any]:
    decided = [r for r in requests if r.get("decision")]
    by_human = [r for r in decided if r.get("resolved_by")]
    by_system = [r for r in decided if not r.get("resolved_by")]
    # Every request a human had to touch: still in the queue, or resolved by one.
    escalated = [r for r in requests if r.get("workflow_status") in HUMAN_QUEUE_STATUSES] + by_human

    handling_hours = []
    for record in by_human:
        created, resolved = _parse(record.get("created_at")), _parse(record.get("resolved_at"))
        if created and resolved and resolved >= created:
            handling_hours.append((resolved - created).total_seconds() / 3600)

    window_start = today - timedelta(days=days - 1)
    trend = {window_start + timedelta(days=offset): {"created": 0, "auto": 0, "human": 0} for offset in range(days)}
    for record in requests:
        day = _day(record)
        if day in trend:
            trend[day]["created"] += 1
            if record.get("decision"):
                trend[day]["human" if record.get("resolved_by") else "auto"] += 1

    approved = [r for r in decided if r.get("decision") in APPROVED_DECISIONS]
    decision_counts = Counter(r["decision"] for r in decided)
    return {
        "as_of": today.isoformat(),
        "window_days": days,
        "totals": {
            "requests": len(requests),
            "open": sum(1 for r in requests if r.get("workflow_status") != "Closed"),
            "awaiting_human": sum(1 for r in requests if r.get("workflow_status") in HUMAN_QUEUE_STATUSES),
            "decided": len(decided),
            "decided_by_system": len(by_system),
            "decided_by_human": len(by_human),
        },
        "rates": {
            # Share of decided requests that never needed a human.
            "automation_rate": round(len(by_system) / len(decided), 4) if decided else None,
            # Share of all requests that ended up with a human at some point.
            "escalation_rate": round(len(escalated) / len(requests), 4) if requests else None,
            "approval_rate": round(len(approved) / len(decided), 4) if decided else None,
        },
        "approved_amount_total": round(sum(float(r.get("approved_amount") or 0) for r in approved), 2),
        "avg_human_handling_hours": round(sum(handling_hours) / len(handling_hours), 1) if handling_hours else None,
        "decisions": [{"decision": key, "count": count} for key, count in decision_counts.most_common()],
        "reasons": [
            {"reason": reason, "count": count}
            for reason, count in Counter(r.get("request_reason") or "其他" for r in requests).most_common()
        ],
        "statuses": [
            {"status": status, "count": count}
            for status, count in Counter(r.get("workflow_status") for r in requests).most_common()
        ],
        "daily": [{"date": day.isoformat(), **values} for day, values in trend.items()],
        "returns": [
            {"status": status, "count": count}
            for status, count in Counter(r.get("workflow_status") for r in return_orders).most_common()
        ],
        "actions": [{"status": status, "count": count} for status, count in sorted(action_status_counts.items())],
    }
