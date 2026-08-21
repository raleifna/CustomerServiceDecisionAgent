"""Internal, staff-facing endpoints for the human-in-the-loop workbench.

Separate from the customer-facing /chat surface in main.py: these operate on
service_requests/evidence directly rather than through a conversation turn,
and they are the only place a human decision (as opposed to an algorithmic
one) gets written back.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, status

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
    ReturnInspectionRequest,
)
from src.backend.graph.workflow import customer_service_graph
from src.backend.llm import telemetry
from src.backend.repositories.evidence_repository import (
    list_evidence_records,
    list_pending_evidence_for_staff_queue,
    review_evidence,
)
from src.backend.repositories.mock_db_repository import (
    get_customer,
    get_logistics,
    get_order,
    get_product,
)
from src.backend.repositories.service_request_repository import (
    get_service_request,
    list_staff_service_request_queue,
    claim_service_request,
)
from src.backend.repositories.decision_lifecycle_repository import (
    list_failed_actions,
    resolve_human_decision,
    retry_failed_action,
)
from src.backend.repositories.audit_repository import write_audit_log
from src.backend.api.staff_auth import StaffPrincipal, require_roles
from src.backend.repositories.order_item_repository import (
    get_order_items,
    get_service_request_items,
)
from src.backend.repositories.return_order_repository import receive_return, inspect_return


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
    return "/uploads/" + storage_path.removeprefix("data/uploads/")


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
    )


@router.get("/telemetry")
def get_staff_telemetry(
    _: StaffPrincipal = Depends(require_roles("admin")),
) -> dict:
    """LLM call health — in-process counters, see llm/telemetry.py."""
    return telemetry.snapshot()


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
    return StaffQueueResponse(
        service_requests=service_requests,
        evidence=evidence,
        failed_actions=failed_actions,
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

    order = get_order(service_request.get("order_id"))
    customer = get_customer(service_request.get("customer_id"))
    request_items = get_service_request_items(request_id)
    order_items = get_order_items(service_request.get("order_id"))
    target_item_id = request_items[0].get("order_item_id") if len(request_items) == 1 else None
    target_item = next(
        (item for item in order_items if item.get("order_item_id") == target_item_id),
        None,
    )
    product = get_product(target_item.get("product_id")) if target_item else None
    logistics = (
        get_logistics(service_request.get("order_id")) if order else []
    )
    evidence = [
        _to_evidence_summary(record)
        for record in list_evidence_records(request_id)
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
