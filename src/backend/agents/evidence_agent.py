from src.backend.graph.state import CustomerServiceState

def evidence_agent_node(
    state: CustomerServiceState,
) -> CustomerServiceState:
    submitted_evi = state.get("submitted_evi")

    if submitted_evi is None:
        cur_errors = state.get("errors", [])

        return {
            "errors": cur_errors + ["Evidence Agent 缺少 submitted_evi"],
        }
    
    evidence_ids = []
    verification_statuses = []
    review_findings = []
    supporting_facts = []
    review_confidences = []
    supporting_confidences = []
    for record in submitted_evi:
        evidence_id = record.get("evidence_id")

        if evidence_id:
            evidence_ids.append(evidence_id)

        status = record.get("verification_status")

        if status:
            verification_statuses.append(status)

        facts = record.get("verified_facts", [])

        if isinstance(facts, list):
            review_findings.extend(facts)

            if status in {
                "Verified",
                "MachineReviewed",
            }:
                supporting_facts.extend(facts)

        confidence = record.get(
            "verification_confidence"
        )

        if isinstance(confidence, (int, float)):
            review_confidences.append(confidence)

            if status in {
                "Verified",
                "MachineReviewed",
            }:
                supporting_confidences.append(
                    confidence
                )

    evidence_available = len(submitted_evi) > 0
    has_pending_evidence = (
        "Pending" in verification_statuses
    )
    has_rejected_evidence = (
        "Rejected" in verification_statuses
    )
    has_machine_reviewed_evidence = (
        "MachineReviewed" in verification_statuses
    )
    has_verified_evidence = (
        "Verified" in verification_statuses
    )
    requires_human_review = (
        "NeedsHumanReview" in verification_statuses
    )
    has_usable_evidence = (
        has_machine_reviewed_evidence
        or has_verified_evidence
    )

    if review_confidences:
        max_review_confidence = max(
            review_confidences
        )
    else:
        max_review_confidence = None

    if supporting_confidences:
        max_supporting_confidence = max(
            supporting_confidences
        )
    else:
        max_supporting_confidence = None

    return {
        "evidence_review": {
            "evidence_available": evidence_available,
            "evidence_count": len(submitted_evi),
            "evidence_ids": evidence_ids,
            "verification_statuses": (
                verification_statuses
            ),
            "review_findings": review_findings,
            "supporting_facts": supporting_facts,
            "has_pending_evidence": (
                has_pending_evidence
            ),
            "has_rejected_evidence": (
                has_rejected_evidence
            ),
            "has_machine_reviewed_evidence": (
                has_machine_reviewed_evidence
            ),
            "has_verified_evidence": (
                has_verified_evidence
            ),
            "has_usable_evidence": (
                has_usable_evidence
            ),
            "requires_human_review": (
                requires_human_review
            ),
            "max_review_confidence": (
                max_review_confidence
            ),
            "max_supporting_confidence": (
                max_supporting_confidence
            ),
        }
    }