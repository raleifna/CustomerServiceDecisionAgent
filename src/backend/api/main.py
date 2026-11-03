import json
import logging
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from src.backend.api import customer_auth, live_chat, staff, staff_auth
from src.backend.api.auth_tokens import signed_upload_url, verify_file_signature
from src.backend.api.customer_auth import current_customer_id
from src.backend.api.schemas import (
    ChatRequest,
    ChatResponse,
    CustomerServiceRequestStatus,
    CustomerServiceRequestProgress,
    ReturnShipmentRequest,
    EvidenceSummary,
    HealthResponse,
    OrderDetail,
    OrderSummary,
    ProductCard,
    ProductCategory,
    ProductDetail,
)
from src.backend.graph.workflow import (
    compile_conversation_graph,
    customer_service_conversation_graph,
)
from src.backend.repositories.mock_db_repository import (
    MOCK_DB_DIR,
    PROJECT_ROOT,
    get_customer,
    get_order,
    get_orders_by_customer_id,
    get_logistics,
    get_logistics_tracking,
    load_table,
)
from src.backend.repositories.product_catalog_repository import (
    get_storefront_product_card,
    get_storefront_product_detail,
    list_storefront_categories,
    list_storefront_products,
)
from src.backend.repositories.postgres_repository import (
    database_is_available,
    get_database_url,
    setup_application_schema,
)
from src.backend.repositories.evidence_repository import list_evidence_records
from src.backend.repositories.service_request_repository import (
    get_completed_refund_amount,
    get_service_request,
    get_service_requests_by_customer_id,
    withdraw_service_request,
)
from src.backend.repositories.decision_lifecycle_repository import list_actions_for_request
from src.backend.services.evidence_upload_service import store_customer_evidence
from src.backend.repositories.order_item_repository import get_order_items
from src.backend.repositories.return_order_repository import (
    get_return_order_for_request, submit_return_shipment,
)
from src.backend.config.business_rules import BusinessRulesError, load_business_rules
from src.backend.decision.rule_engine import DecisionRulesError, load_decision_rules


logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Use PostgreSQL in deployed environments and memory for local fallback."""
    database_url = get_database_url()
    pool = None

    if database_url:
        from langgraph.checkpoint.postgres import PostgresSaver
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool

        setup_application_schema(database_url)
        pool = ConnectionPool(
            conninfo=database_url,
            min_size=1,
            max_size=10,
            kwargs={"autocommit": True, "row_factory": dict_row},
            open=True,
        )
        checkpointer = PostgresSaver(pool)
        checkpointer.setup()
        app.state.conversation_graph = compile_conversation_graph(checkpointer)
        logger.info("PostgreSQL persistence is enabled")
    else:
        app.state.conversation_graph = customer_service_conversation_graph
        logger.warning("DATABASE_URL is not configured; using in-memory persistence")

    try:
        yield
    finally:
        if pool is not None:
            pool.close()


def _cors_origins() -> list[str]:
    configured = os.getenv(
        "CORS_ORIGINS",
        "http://localhost:3000,http://localhost:5173",
    )
    return [origin.strip() for origin in configured.split(",") if origin.strip()]


app = FastAPI(
    title="Customer Service Decision Agent API",
    version="0.1.0",
    description="Safe HTTP interface for the multi-agent customer service graph.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

app.include_router(customer_auth.router)
app.include_router(staff_auth.router)
app.include_router(live_chat.customer_router)
app.include_router(live_chat.staff_router)
app.include_router(staff.router)

_UPLOADS_DIR = (PROJECT_ROOT / "data" / "uploads").resolve()
_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)


@app.get("/uploads/{file_path:path}", include_in_schema=False)
def get_uploaded_file(file_path: str, exp: str | None = None, sig: str | None = None) -> FileResponse:
    """Evidence images are private: only URLs signed by this API (handed to
    the owning customer or to staff, valid for an hour) can fetch them."""
    if not verify_file_signature(file_path, exp, sig):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="文件链接无效或已过期。")
    target = (_UPLOADS_DIR / file_path).resolve()
    if not target.is_relative_to(_UPLOADS_DIR) or not target.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="文件不存在。")
    return FileResponse(target, headers={"Cache-Control": "private, max-age=3600"})


def _evidence_file_url(storage_path: str) -> str:
    return signed_upload_url(storage_path)


def _to_evidence_summary(record: dict) -> EvidenceSummary:
    return EvidenceSummary(
        **{
            key: record.get(key)
            for key in (
                "evidence_id", "request_id", "evidence_type", "submitted_by",
                "submitted_date", "original_filename", "media_type",
                "file_size_bytes", "verification_status", "verified_facts",
                "verification_confidence", "customer_description", "review_notes",
            )
        },
        file_url=_evidence_file_url(record["storage_path"]) if "storage_path" in record else "",
    )


def _safe_chat_response(
    conversation_id: str,
    graph_result: dict,
) -> ChatResponse:
    conversation_result = graph_result.get("conversation_result") or {}
    query_result = graph_result.get("query_result") or {}
    final_result = graph_result.get("final_result") or {}
    action_result = graph_result.get("action_result") or {}
    service_request = graph_result.get("service_request") or {}
    context = graph_result.get("conversation_context") or {}

    customer_message = graph_result.get("customer_response")
    if not isinstance(customer_message, str) or not customer_message.strip():
        customer_message = "当前暂时无法完成处理，请稍后重试或联系人工客服。"

    return ChatResponse(
        conversation_id=conversation_id,
        message=customer_message,
        intent=conversation_result.get("conversation_intent"),
        order_id=(
            conversation_result.get("order_id")
            or query_result.get("order_id")
            or final_result.get("order_id")
            or action_result.get("order_id")
            or context.get("active_order_id")
        ),
        request_id=(
            action_result.get("request_id")
            or
            query_result.get("request_id")
            or final_result.get("request_id")
            or context.get("active_request_id")
        ),
        request_reason=(
            service_request.get("request_reason")
            or final_result.get("request_reason")
        ),
        handling_status=final_result.get("handling_status"),
        decision=final_result.get("decision"),
        action=action_result.get("action"),
        action_id=action_result.get("fulfillment_request_id"),
        handoff_id=action_result.get("handoff_id"),
    )


@app.get("/health", response_model=HealthResponse, tags=["system"])
def health() -> HealthResponse:
    required_files = (
        "orders.json",
        "service_requests.json",
    )
    files_available = all((MOCK_DB_DIR / filename).is_file() for filename in required_files)
    database_url = get_database_url()
    database_available = database_is_available(database_url)
    persistent_runtime = database_available if database_url else True

    try:
        load_business_rules()
        decision_rule_count = len(load_decision_rules())
        rules_status = f"available:{decision_rule_count}"
        rules_available = True
    except (BusinessRulesError, DecisionRulesError):
        logger.exception("Business rule configuration is invalid")
        rules_status = "invalid"
        rules_available = False

    return HealthResponse(
        status="ok" if files_available and persistent_runtime and rules_available else "degraded",
        service="customer-service-decision-agent",
        checks={
            "mock_db": "available" if files_available else "missing_files",
            "persistence": (
                "postgresql"
                if database_available
                else "memory_fallback" if not database_url else "unavailable"
            ),
            "llm_configured": bool(os.getenv("API_KEY")),
            "decision_rules": rules_status,
            "staff_auth": "configured" if os.getenv("STAFF_API_KEYS", "").strip() else "missing",
        },
    )


@app.get("/products", response_model=list[ProductCard], tags=["storefront"])
def list_products(
    category: str | None = None,
    q: str | None = None,
    sort: str | None = None,
) -> list[ProductCard]:
    return [
        ProductCard.model_validate(card)
        for card in list_storefront_products(category=category, query=q, sort=sort)
    ]


@app.get("/product-categories", response_model=list[ProductCategory], tags=["storefront"])
def list_product_categories() -> list[ProductCategory]:
    return [ProductCategory.model_validate(item) for item in list_storefront_categories()]


@app.get("/products/{product_id}", response_model=ProductDetail, tags=["storefront"])
def get_product_detail(product_id: str) -> ProductDetail:
    detail = get_storefront_product_detail(product_id)
    if detail is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="商品不存在。")
    return ProductDetail.model_validate(detail)


# Where customers send no-reason returns. Demo data; shown on the return card.
RETURN_WAREHOUSE = {
    "recipient": "ShopCare 退货仓",
    "phone": "400-800-0000",
    "address": "浙江省杭州市余杭区仓前街道退货处理中心 1 号库",
}


def _customer_return_order(request_id: str) -> dict | None:
    return_order = get_return_order_for_request(request_id)
    if return_order is None:
        return None
    return {**return_order, "return_address": RETURN_WAREHOUSE}


def _order_status(order: dict) -> str:
    if order.get("delivered_date"):
        return "Delivered"
    if order.get("shipped_date"):
        return "Shipped"
    return "PendingShipment"


@app.get("/me/orders", response_model=list[OrderSummary], tags=["me"])
def list_my_orders(customer_id: str = Depends(current_customer_id)) -> list[OrderSummary]:
    result = []
    for order in get_orders_by_customer_id(customer_id):
        product = get_storefront_product_card(order.get("product_id"))
        result.append(
            OrderSummary(
                **order,
                product_category=product["category"] if product else None,
                order_status=_order_status(order),
                product=product,
            )
        )
    # Newest first, like any shop's "我的订单" list.
    result.sort(key=lambda order: order.order_date, reverse=True)
    return result


@app.get("/me/service-requests", response_model=list[CustomerServiceRequestProgress], tags=["me"])
def list_my_service_requests(customer_id: str = Depends(current_customer_id)) -> list[CustomerServiceRequestProgress]:
    result = []
    for request_record in get_service_requests_by_customer_id(customer_id):
        request_id = request_record["request_id"]
        evidence_records = list_evidence_records(request_id)
        reviews = [
            {
                "evidence_id": evidence.get("evidence_id"),
                "verification_status": evidence.get("verification_status"),
                "review_notes": evidence.get("review_notes"),
                "reviewed_at": evidence.get("reviewed_at"),
            }
            for evidence in evidence_records
            if evidence.get("verification_status") in {"Verified", "Rejected"}
        ]
        result.append(CustomerServiceRequestProgress(
            **{key: request_record.get(key) for key in (
                "request_id", "order_id", "request_type", "request_reason",
                "requested_quantity", "request_date", "workflow_status", "decision",
                "approved_quantity", "approved_amount", "resolution_notes", "resolved_at",
            )},
            evidence_reviews=reviews,
            evidence=[_to_evidence_summary(record) for record in evidence_records],
            actions=list_actions_for_request(request_id),
            return_order=_customer_return_order(request_id),
        ))
    return result


@app.get("/me/orders/{order_id}", response_model=OrderDetail, tags=["me"])
def get_my_order_detail(order_id: str, customer_id: str = Depends(current_customer_id)) -> OrderDetail:
    order = get_order(order_id)
    if order is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="订单不存在。",
        )
    if order.get("customer_id") != customer_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="该订单不属于当前客户。",
        )

    product = get_storefront_product_card(order.get("product_id"))
    return OrderDetail(
        **order,
        product_category=product["category"] if product else None,
        order_status=_order_status(order),
        product=product,
        logistics=get_logistics(order_id),
        order_items=get_order_items(order_id),
        refunded_amount=get_completed_refund_amount(order_id),
        tracking=get_logistics_tracking(order_id),
    )


def _owned_service_request(request_id: str, customer_id: str) -> dict:
    service_request = get_service_request(request_id)
    if service_request is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="售后申请不存在。",
        )
    if service_request.get("customer_id") != customer_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="该售后申请不属于当前客户。",
        )
    return service_request


@app.get(
    "/service-requests/{request_id}/status",
    response_model=CustomerServiceRequestStatus,
    tags=["service-requests"],
)
def get_customer_service_request_status(
    request_id: str,
    customer_id: str = Depends(current_customer_id),
) -> CustomerServiceRequestStatus:
    """Return a customer-owned request's latest human/automatic outcome."""
    service_request = _owned_service_request(request_id, customer_id)
    resolved_at = service_request.get("resolved_at")
    evidence_reviews = [
        {
            "evidence_id": evidence.get("evidence_id"),
            "verification_status": evidence.get("verification_status"),
            "review_notes": evidence.get("review_notes"),
            "reviewed_at": evidence.get("reviewed_at"),
        }
        for evidence in list_evidence_records(request_id)
        if evidence.get("verification_status") in {"Verified", "Rejected"}
    ]
    return CustomerServiceRequestStatus(
        request_id=request_id,
        workflow_status=service_request.get("workflow_status", "Pending"),
        decision=service_request.get("decision"),
        approved_amount=service_request.get("approved_amount"),
        resolution_notes=service_request.get("resolution_notes"),
        resolved_at=(resolved_at.isoformat() if hasattr(resolved_at, "isoformat") else resolved_at),
        return_order=_customer_return_order(request_id),
        evidence_reviews=evidence_reviews,
        actions=list_actions_for_request(request_id),
    )


@app.post("/service-requests/{request_id}/withdraw", tags=["service-requests"])
def withdraw_customer_service_request(
    request_id: str,
    customer_id: str = Depends(current_customer_id),
) -> CustomerServiceRequestStatus:
    _owned_service_request(request_id, customer_id)
    withdrawn = withdraw_service_request(request_id, customer_id)
    if withdrawn is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="该申请已完成或已进入退款/履约阶段，当前不能撤销。",
        )
    resolved_at = withdrawn.get("resolved_at")
    return CustomerServiceRequestStatus(
        request_id=request_id,
        workflow_status=withdrawn["workflow_status"],
        decision=withdrawn.get("decision"),
        resolution_notes=withdrawn.get("resolution_notes"),
        resolved_at=resolved_at,
        return_order=None,
        evidence_reviews=[],
    )


@app.post("/service-requests/{request_id}/return-shipment", tags=["service-requests"])
def submit_customer_return_shipment(
    request_id: str,
    payload: ReturnShipmentRequest,
    customer_id: str = Depends(current_customer_id),
) -> dict:
    _owned_service_request(request_id, customer_id)
    try:
        result = submit_return_shipment(request_id, customer_id, payload.carrier, payload.tracking_number)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="退货服务暂时不可用，请稍后重试。") from exc
    if result is None:
        raise HTTPException(status_code=409, detail="退货单不存在，或已经提交过寄回物流。")
    return {**result, "return_address": RETURN_WAREHOUSE}


@app.post(
    "/service-requests/{request_id}/evidence",
    response_model=EvidenceSummary,
    status_code=status.HTTP_201_CREATED,
    tags=["evidence"],
)
def upload_service_request_evidence(
    request_id: str,
    description: str = Form(...),
    file: UploadFile = File(...),
    customer_id: str = Depends(current_customer_id),
) -> EvidenceSummary:
    service_request = _owned_service_request(request_id, customer_id)
    rejected_rounds = sum(
        1
        for evidence in list_evidence_records(request_id)
        if evidence.get("verification_status") == "Rejected"
    )
    if (
        service_request.get("request_reason") in {"质量问题", "商品损坏"}
        and rejected_rounds >= 2
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="已达到两轮补证上限，申请已进入最终人工复核，暂不能继续上传材料。",
        )
    try:
        record = store_customer_evidence(
            upload=file,
            customer_id=customer_id,
            request_id=request_id,
            customer_description=description,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except Exception as exc:
        logger.exception("Evidence upload failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="证据暂时未能保存，请稍后重试。",
        ) from exc
    return _to_evidence_summary(record)


@app.get(
    "/service-requests/{request_id}/evidence",
    response_model=list[EvidenceSummary],
    tags=["evidence"],
)
def list_service_request_evidence(
    request_id: str,
    customer_id: str = Depends(current_customer_id),
) -> list[EvidenceSummary]:
    _owned_service_request(request_id, customer_id)
    return [
        _to_evidence_summary(record)
        for record in list_evidence_records(request_id)
    ]


@app.post(
    "/chat",
    response_model=ChatResponse,
    status_code=status.HTTP_200_OK,
    tags=["chat"],
)
def chat(
    payload: ChatRequest,
    request: Request,
    customer_id: str = Depends(current_customer_id),
) -> ChatResponse:
    if payload.order_id:
        order = get_order(payload.order_id)
        if order is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="订单不存在。",
            )
        if order.get("customer_id") != customer_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="该订单不属于当前客户。",
            )

    # A browser-generated conversation ID is not globally trusted. Namespacing
    # it by customer prevents two mock users from sharing the same checkpoint.
    graph_thread_id = f"{customer_id}:{payload.conversation_id}"
    graph_config = {"configurable": {"thread_id": graph_thread_id}}

    try:
        snapshot = request.app.state.conversation_graph.get_state(graph_config)
        existing_context = dict(
            (snapshot.values or {}).get("conversation_context") or {}
        )
        existing_context["authenticated_customer_id"] = customer_id
        # A request can be withdrawn from the separate progress page, outside
        # the conversation graph. Do not let that closed request remain the
        # active chat request on the next turn.
        active_request_id = existing_context.get("active_request_id")
        if active_request_id:
            active_request = get_service_request(active_request_id)
            if (
                active_request is None
                or active_request.get("customer_id") != customer_id
                or active_request.get("workflow_status") == "Closed"
            ):
                existing_context.pop("active_request_id", None)
                existing_context.pop("pending_action", None)
                existing_context.pop("pending_service_request", None)
                existing_context.pop("pending_order_confirmation", None)
        if payload.order_id:
            if existing_context.get("active_order_id") != payload.order_id:
                existing_context["order_context_confirmed"] = False
            existing_context["active_order_id"] = payload.order_id

        result = request.app.state.conversation_graph.invoke(
            {
                "user_message": payload.message,
                "conversation_context": existing_context,
                "conversation_id": graph_thread_id,
            },
            config=graph_config,
        )
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("Invalid business data while processing chat: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="业务数据暂时不可用，请稍后重试。",
        ) from exc
    except Exception as exc:
        logger.exception("Unhandled chat processing error")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="当前暂时无法处理该请求，请稍后重试。",
        ) from exc

    return _safe_chat_response(payload.conversation_id, result)
