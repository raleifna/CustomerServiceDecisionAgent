"""Internal, staff-facing endpoints for the human-in-the-loop workbench.

Separate from the customer-facing /chat surface in main.py: these operate on
service_requests/evidence directly rather than through a conversation turn,
and they are the only place a human decision (as opposed to an algorithmic
one) gets written back.
"""
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse

from src.backend.api.schemas import (
    EvidenceReviewRequest,
    EvidenceReviewResponse,
    ServiceRequestResolutionRequest,
    ServiceRequestClaimRequest,
    StaffEvidenceSummary,
    StaffQueueResponse,
    StaffServiceRequestDetail,
    StaffServiceRequestSummary,
    StaffActionSummary,
    StaffReturnOrder,
    ReturnInspectionRequest,
)
from src.backend.graph.workflow import customer_service_graph
from src.backend.llm import telemetry
from src.backend.repositories.evidence_repository import (
    list_evidence_records,
    list_pending_evidence_for_staff_queue,
    review_evidence,
)

from src.backend.repositories.service_request_repository import (
    get_service_request,
    get_service_requests_by_customer_id,
    list_staff_service_request_queue,
    list_staff_service_requests,
    claim_service_request,
)
from src.backend.repositories.decision_lifecycle_repository import (
    count_actions_by_status,
    list_actions_for_request,
    list_failed_actions,
    resolve_human_decision,
    retry_failed_action,
)
from src.backend.repositories.audit_repository import write_audit_log
from src.backend.config.business_rules import load_business_rules
from src.backend.services.business_clock import get_business_clock
from src.backend.services import commerce_data
from src.backend.services.ai_review_service import compute_ai_review, evaluation_candidates
from src.backend.services.staff_stats_service import compute_staff_stats
from src.backend.api.auth_tokens import signed_upload_url
from src.backend.api.staff_auth import StaffPrincipal, require_roles
from src.backend.repositories.return_order_repository import (
    get_return_order_for_request,
    inspect_return,
    list_return_orders,
    receive_return,
)


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/staff", tags=["staff"])


@router.post("/return-orders/{return_order_id}/receive")
def receive_staff_return(return_order_id: str, _: StaffPrincipal = Depends(require_roles("reviewer", "admin"))) -> dict:
    result = receive_return(return_order_id)
    if result is None:
        raise HTTPException(status_code=409, detail="退货单不存在或尚未进入寄回运输状态。")
    return result


@router.post("/return-orders/{return_order_id}/inspect")
def inspect_staff_return(return_order_id: str, payload: ReturnInspectionRequest, _: StaffPrincipal = Depends(require_roles("reviewer", "admin"))) -> dict:
    result = inspect_return(return_order_id, payload.accepted, payload.notes)
    if result is None:
        raise HTTPException(status_code=409, detail="退货单不存在或当前状态不能验收。")
    return result


@router.get("/me")
def get_staff_identity(
    principal: StaffPrincipal = Depends(require_roles("viewer", "reviewer", "admin")),
) -> dict:
    return {"staff_id": principal.staff_id, "roles": sorted(principal.roles)}


def _evidence_file_url(storage_path: str) -> str:
    return signed_upload_url(storage_path)


def _to_evidence_summary(record: dict) -> StaffEvidenceSummary:
    return StaffEvidenceSummary(
        evidence_id=record["evidence_id"],
        request_id=record["request_id"],
        original_filename=record["original_filename"],
        media_type=record["media_type"],
        verification_status=record["verification_status"],
        submitted_date=record["submitted_date"],
        customer_description=record.get("customer_description"),
        file_url=_evidence_file_url(record["storage_path"])
        if "storage_path" in record
        else "",
        review_notes=record.get("review_notes"),
        reviewed_by=record.get("reviewed_by"),
        reviewed_at=record.get("reviewed_at"),
    )


def _to_service_request_summary(
    record: dict,
) -> StaffServiceRequestSummary:
    order = commerce_data.get_order(record["order_id"])
    return StaffServiceRequestSummary(
        request_id=record["request_id"],
        order_id=record["order_id"],
        customer_id=record["customer_id"],
        request_type=record["request_type"],
        request_reason=record["request_reason"],
        target_order_item_id=record.get("target_order_item_id"),
        requested_quantity=record["requested_quantity"],
        request_date=record["request_date"],
        workflow_status=record["workflow_status"],
        decision=record.get("decision"),
        reasons=record.get("reasons") or [],
        rule_hits=record.get("rule_hits") or [],
        required_actions=record.get("required_actions") or [],
        assigned_to=record.get("assigned_to"),
        lease_expires_at=record.get("lease_expires_at"),
        row_version=record.get("row_version", 0),
        approved_quantity=record.get("approved_quantity"),
        approved_amount=record.get("approved_amount"),
        decision_confidence=record.get("decision_confidence"),
        resolved_by=record.get("resolved_by"),
        resolution_notes=record.get("resolution_notes"),
        resolved_at=_iso(record.get("resolved_at")),
        created_at=_iso(record.get("created_at")),
        updated_at=_iso(record.get("updated_at")),
        product=commerce_data.product_card_for_order(record["order_id"], order, include_v2=True),
        order_amount=float(order["amount"]) if order else None,
        ai_recommendation=record.get("ai_recommendation"),
    )


def _iso(value) -> str | None:
    if value is None or isinstance(value, str):
        return value
    return value.isoformat()


@router.get("/telemetry")
def get_staff_telemetry(
    _: StaffPrincipal = Depends(require_roles("admin")),
) -> dict:
    """LLM call health — in-process counters, see llm/telemetry.py."""
    return telemetry.snapshot()


@router.get("/stats")
def get_staff_stats(
    days: int = Query(default=14, ge=7, le=60),
    principal: StaffPrincipal = Depends(require_roles("viewer", "reviewer", "admin")),
) -> dict:
    """Dashboard aggregates; LLM call health is included for admins only,
    matching /staff/telemetry's permission."""
    automation = load_business_rules()["automation"]
    stats = compute_staff_stats(
        list_staff_service_requests(limit=5000),
        return_orders=list_return_orders(None),
        action_status_counts=count_actions_by_status(),
        today=get_business_clock().today(),
        days=days,
        now=datetime.now(timezone.utc),
        sla_hours=automation["human_review_sla_hours"],
        return_transit_alert_days=automation["return_transit_alert_days"],
    )
    stats["llm"] = telemetry.snapshot() if "admin" in principal.roles else None
    return stats


@router.get("/ai-review")
def get_ai_review(
    _: StaffPrincipal = Depends(require_roles("viewer", "reviewer", "admin")),
) -> dict:
    """Agreement between the AI's shadow suggestion and the human outcome."""
    return compute_ai_review(list_staff_service_requests(limit=5000))


@router.get("/ai-review/export")
def export_ai_review_cases(
    _: StaffPrincipal = Depends(require_roles("reviewer", "admin")),
) -> JSONResponse:
    """Human overrides as candidate regression cases (JSON download)."""
    return JSONResponse(
        evaluation_candidates(list_staff_service_requests(limit=5000)),
        headers={"Content-Disposition": 'attachment; filename="human_override_candidates.json"'},
    )


@router.get("/queue", response_model=StaffQueueResponse)
def get_staff_queue(
    _: StaffPrincipal = Depends(require_roles("viewer", "reviewer", "admin")),
) -> StaffQueueResponse:
    service_requests = [
        _to_service_request_summary(record)
        for record in list_staff_service_request_queue()
    ]
    evidence = [
        _to_evidence_summary(record)
        for record in list_pending_evidence_for_staff_queue()
    ]
    failed_actions = [
        StaffActionSummary.model_validate(record) for record in list_failed_actions()
    ]
    automation = load_business_rules()["automation"]
    return StaffQueueResponse(
        service_requests=service_requests,
        evidence=evidence,
        failed_actions=failed_actions,
        sla_hours=automation["human_review_sla_hours"],
        return_transit_alert_days=automation["return_transit_alert_days"],
    )


@router.post("/actions/{action_id}/retry", response_model=StaffActionSummary)
def retry_staff_action(
    action_id: str,
    principal: StaffPrincipal = Depends(require_roles("reviewer", "admin")),
) -> StaffActionSummary:
    try:
        action = retry_failed_action(
            action_id,
            staff_id=principal.staff_id,
            staff_role=sorted(principal.roles)[0],
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if action is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="任务不存在或当前状态不允许重试。",
        )
    return StaffActionSummary.model_validate(action)


@router.get("/service-requests", response_model=list[StaffServiceRequestSummary])
def list_staff_case_history(
    workflow_status: str | None = Query(default=None, max_length=64),
    q: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=200, ge=1, le=500),
    _: StaffPrincipal = Depends(require_roles("viewer", "reviewer", "admin")),
) -> list[StaffServiceRequestSummary]:
    """All service requests (not just the human queue) for search/history."""
    return [
        _to_service_request_summary(record)
        for record in list_staff_service_requests(workflow_status=workflow_status, query=q, limit=limit)
    ]


RETURN_QUEUE_STATUSES = ["AwaitingShipment", "InTransit", "AwaitingInspection"]


@router.get("/return-orders", response_model=list[StaffReturnOrder])
def list_staff_return_orders(
    scope: str = Query(default="open", pattern="^(open|all)$"),
    _: StaffPrincipal = Depends(require_roles("viewer", "reviewer", "admin")),
) -> list[StaffReturnOrder]:
    """Return orders awaiting the warehouse (scope=open) or all of them."""
    result = []
    for record in list_return_orders(RETURN_QUEUE_STATUSES if scope == "open" else None):
        request = get_service_request(record["request_id"]) or {}
        result.append(StaffReturnOrder(
            **record,
            request_reason=request.get("request_reason"),
            product=commerce_data.product_card_for_order(record["order_id"], commerce_data.get_order(record["order_id"]), include_v2=True),
        ))
    return result


@router.get(
    "/service-requests/{request_id}",
    response_model=StaffServiceRequestDetail,
)
def get_staff_service_request_detail(
    request_id: str,
    _: StaffPrincipal = Depends(require_roles("viewer", "reviewer", "admin")),
) -> StaffServiceRequestDetail:
    service_request = get_service_request(request_id)
    if service_request is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="售后申请不存在。",
        )

    order = commerce_data.get_order(service_request.get("order_id"))
    customer = commerce_data.get_customer(service_request.get("customer_id"))
    request_items = commerce_data.get_service_request_items(request_id)
    order_items = commerce_data.get_order_items(service_request.get("order_id"))
    target_item_id = request_items[0].get("order_item_id") if len(request_items) == 1 else None
    target_item = next(
        (item for item in order_items if item.get("order_item_id") == target_item_id),
        None,
    )
    product = commerce_data.get_product_facts(target_item.get("product_id")) if target_item else None
    logistics = (
        commerce_data.get_logistics_events(service_request.get("order_id")) if order else []
    )
    evidence = [
        _to_evidence_summary(record)
        for record in list_evidence_records(request_id)
    ]

    return_order = get_return_order_for_request(request_id)
    customer_requests = [
        _to_service_request_summary(record)
        for record in get_service_requests_by_customer_id(service_request["customer_id"])
        if record["request_id"] != request_id
    ]

    return StaffServiceRequestDetail(
        service_request=_to_service_request_summary(service_request),
        order=order,
        customer=customer,
        product=product,
        logistics=logistics,
        evidence=evidence,
        order_items=order_items,
        request_items=request_items,
        tracking=commerce_data.get_tracking(service_request["order_id"]) if order else None,
        return_order={key: _iso(value) if hasattr(value, "isoformat") else (float(value) if key == "refundable_amount" else value)
                      for key, value in return_order.items()} if return_order else None,
        actions=list_actions_for_request(request_id),
        customer_requests=customer_requests[:10],
    )


@router.post(
    "/service-requests/{request_id}/claim",
    response_model=StaffServiceRequestSummary,
)
def claim_staff_service_request(
    request_id: str,
    payload: ServiceRequestClaimRequest,
    principal: StaffPrincipal = Depends(require_roles("reviewer", "admin")),
) -> StaffServiceRequestSummary:
    try:
        claimed = claim_service_request(
            request_id, staff_id=principal.staff_id, lease_minutes=payload.lease_minutes
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if claimed is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="工单不存在、状态不可领取，或已由其他客服领取。",
        )
    write_audit_log(
        actor_id=principal.staff_id,
        actor_role=sorted(principal.roles)[0],
        action="ClaimServiceRequest",
        resource_type="ServiceRequest",
        resource_id=request_id,
        outcome="Succeeded",
        details={"lease_minutes": payload.lease_minutes},
    )
    return _to_service_request_summary(claimed)


@router.post(
    "/service-requests/{request_id}/resolve",
    response_model=StaffServiceRequestSummary,
)
def resolve_staff_service_request(
    request_id: str,
    payload: ServiceRequestResolutionRequest,
    principal: StaffPrincipal = Depends(require_roles("reviewer", "admin")),
) -> StaffServiceRequestSummary:
    service_request = get_service_request(request_id)
    if service_request is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="售后申请不存在。",
        )
    if service_request.get("workflow_status") == "Closed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="该申请已经关闭，无需再次处理。",
        )

    try:
        resolve_human_decision(
            request_id,
            decision=payload.decision,
            approved_quantity=payload.approved_quantity,
            approved_amount=payload.approved_amount,
            staff_id=principal.staff_id,
            staff_role=sorted(principal.roles)[0],
            expected_version=payload.expected_version,
            resolution_notes=payload.resolution_notes,
        )
        resolved = get_service_request(request_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    if resolved is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="售后申请不存在。",
        )
    return _to_service_request_summary(resolved)


@router.post(
    "/evidence/{evidence_id}/review",
    response_model=EvidenceReviewResponse,
)
def review_staff_evidence(
    evidence_id: str,
    payload: EvidenceReviewRequest,
    principal: StaffPrincipal = Depends(require_roles("reviewer", "admin")),
) -> EvidenceReviewResponse:
    try:
        reviewed = review_evidence(
            evidence_id,
            verification_status=payload.verification_status,
            reviewed_by=principal.staff_id,
            review_notes=payload.review_notes,
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    if reviewed is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="证据记录不存在。",
        )

    write_audit_log(
        actor_id=principal.staff_id,
        actor_role=sorted(principal.roles)[0],
        action="ReviewEvidence",
        resource_type="Evidence",
        resource_id=evidence_id,
        outcome="Succeeded",
        details={"verification_status": payload.verification_status},
    )

    # Evidence moving out of Pending can unblock the completeness gate — give
    # the graph a chance to auto-complete the request now instead of making a
    # human also work out the refund amount by hand.
    requeued_decision = False
    try:
        customer_service_graph.invoke({"request_id": reviewed["request_id"]})
        requeued_decision = True
    except Exception:
        logger.exception(
            "Failed to re-run decision graph after evidence review for %s",
            reviewed["request_id"],
        )

    return EvidenceReviewResponse(
        evidence=_to_evidence_summary(reviewed),
        requeued_decision=requeued_decision,
    )
