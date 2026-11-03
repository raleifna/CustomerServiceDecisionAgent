"""How often do human reviewers agree with the AI?

For every request a human resolved, compare the human outcome with the AI's
shadow suggestion recorded at escalation time (decision/engine.py
shadow_recommendation). The disagreements are the most valuable signal this
system produces: each one is either a rule that is too strict / too lenient
or a reviewer inconsistency, and each is a ready-made regression case
(see `evaluation_candidates`).

Pure functions over already-loaded request rows, like staff_stats_service.
"""

from collections import Counter, defaultdict
from typing import Any

APPROVING = {"Approve", "PartialApprove"}
CLOSING = {"Reject", "NoActionRequired"}
AMOUNT_TOLERANCE = 0.01


def classify(ai: dict[str, Any] | None, human_decision: str | None, human_amount: float | None) -> str:
    """One of: no_suggestion | agree | amount_differs | ai_more_lenient |
    ai_more_strict | other."""
    if not ai or not ai.get("decision"):
        return "no_suggestion"
    ai_decision = ai["decision"]
    if ai_decision == human_decision:
        if ai_decision in APPROVING and ai.get("approved_amount") is not None and human_amount is not None:
            if abs(float(ai["approved_amount"]) - float(human_amount)) > AMOUNT_TOLERANCE:
                return "amount_differs"
        return "agree"
    if ai_decision in APPROVING and human_decision in APPROVING:
        return "amount_differs"
    if ai_decision in APPROVING and human_decision in CLOSING:
        return "ai_more_lenient"
    if ai_decision in CLOSING and human_decision in APPROVING:
        return "ai_more_strict"
    return "other"


def _human_resolved(requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in requests if r.get("resolved_by") and r.get("decision")]


def compute_ai_review(requests: list[dict[str, Any]], *, case_limit: int = 50) -> dict[str, Any]:
    resolved = _human_resolved(requests)
    rows = []
    for record in resolved:
        ai = record.get("ai_recommendation") or None
        rows.append((record, ai, classify(ai, record.get("decision"), record.get("approved_amount"))))

    compared = [row for row in rows if row[2] != "no_suggestion"]
    agreed = [row for row in compared if row[2] == "agree"]

    by_reason: dict[str, Counter] = defaultdict(Counter)
    by_rule: dict[str, Counter] = defaultdict(Counter)
    for record, ai, outcome in compared:
        by_reason[record.get("request_reason") or "其他"][outcome == "agree"] += 1
        by_rule[ai.get("matched_rule_id") or "unknown"][outcome == "agree"] += 1

    def breakdown(groups: dict[str, Counter], key: str) -> list[dict[str, Any]]:
        result = [
            {key: name, "total": counts[True] + counts[False], "agree": counts[True],
             "agreement_rate": round(counts[True] / (counts[True] + counts[False]), 4)}
            for name, counts in groups.items()
        ]
        return sorted(result, key=lambda item: (item["agreement_rate"], -item["total"]))

    disagreements = sorted(
        (row for row in compared if row[2] != "agree"),
        key=lambda row: str(row[0].get("resolved_at") or ""),
        reverse=True,
    )
    return {
        "totals": {
            "human_resolved": len(resolved),
            "with_suggestion": len(compared),
            "without_suggestion": len(rows) - len(compared),
            "agree": len(agreed),
            "disagree": len(compared) - len(agreed),
        },
        "agreement_rate": round(len(agreed) / len(compared), 4) if compared else None,
        "outcomes": [{"outcome": name, "count": count} for name, count in Counter(row[2] for row in rows).most_common()],
        "matrix": [
            {"ai": ai_decision, "human": human_decision, "count": count}
            for (ai_decision, human_decision), count in Counter(
                (ai.get("decision"), record.get("decision")) for record, ai, _ in compared
            ).most_common()
        ],
        "by_reason": breakdown(by_reason, "reason"),
        "by_rule": breakdown(by_rule, "rule"),
        "disagreements": [
            {
                "request_id": record["request_id"],
                "order_id": record.get("order_id"),
                "request_reason": record.get("request_reason"),
                "outcome": outcome,
                "ai_decision": ai.get("decision"),
                "ai_amount": ai.get("approved_amount"),
                "ai_rule": ai.get("matched_rule_id"),
                "ai_confidence": ai.get("decision_confidence"),
                "human_decision": record.get("decision"),
                "human_amount": record.get("approved_amount"),
                "resolved_by": record.get("resolved_by"),
                "resolved_at": record.get("resolved_at"),
                "resolution_notes": record.get("resolution_notes"),
            }
            for record, ai, outcome in disagreements[:case_limit]
        ],
    }


def evaluation_candidates(requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every human override as a candidate regression case.

    The shape follows data/evaluation/single_turn_cases.json (input +
    expected_paths), plus the request snapshot needed to rebuild it as a
    fixture -- live requests are not in the JSON fixtures, so a candidate has
    to be curated (fixture rows added) before the harness can replay it.
    """
    candidates = []
    for record in _human_resolved(requests):
        ai = record.get("ai_recommendation") or None
        outcome = classify(ai, record.get("decision"), record.get("approved_amount"))
        if outcome in {"agree", "no_suggestion"}:
            continue
        candidates.append({
            "case_id": f"HUMAN_OVERRIDE_{record['request_id']}",
            "category": "human_override",
            "status": "candidate_needs_fixture",
            "input": {"request_id": record["request_id"]},
            "expected_paths": {
                "final_result.decision": record.get("decision"),
                "final_result.approved_amount": record.get("approved_amount"),
            },
            "ai_suggestion": ai,
            "outcome": outcome,
            "human_notes": record.get("resolution_notes"),
            "snapshot": {
                key: record.get(key)
                for key in ("order_id", "customer_id", "request_type", "request_reason",
                            "requested_quantity", "request_date", "reasons", "rule_hits")
            },
        })
    return candidates
