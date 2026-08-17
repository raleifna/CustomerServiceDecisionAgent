import json
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware

from src.backend.api.schemas import (
    ChatRequest,
    ChatResponse,
    CustomerSummary,
    EvidenceSummary,
    HealthResponse,
    OrderDetail,
    OrderSummary,
)
from src.backend.graph.workflow import (
    compile_conversation_graph,
    customer_service_conversation_graph,
)
from src.backend.repositories.mock_db_repository import (
    MOCK_DB_DIR,
    get_customer,
    get_order,
    get_orders_by_customer_id,
    get_logistics,
    get_product,
    load_table,
)
from src.backend.repositories.postgres_repository import (
    database_is_available,
    get_database_url,
    setup_application_schema,
)
from src.backend.repositories.evidence_repository import list_evidence_records
from src.backend.repositories.service_request_repository import get_service_request
from src.backend.services.evidence_upload_service import store_customer_evidence


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


def _safe_chat_response(
    conversation_id: str,
    graph_result: dict,
) -> ChatResponse:
    conversation_result = graph_result.get("conversation_result") or {}
    query_result = graph_result.get("query_result") or {}
    final_result = graph_result.get("final_result") or {}
    action_result = graph_result.get("action_result") or {}
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
        handling_status=final_result.get("handling_status"),
        decision=final_result.get("decision"),
        action=action_result.get("action"),
        action_id=action_result.get("fulfillment_request_id"),
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

    return HealthResponse(
        status="ok" if files_available and persistent_runtime else "degraded",
        service="customer-service-decision-agent",
        checks={
            "mock_db": "available" if files_available else "missing_files",
            "persistence": (
                "postgresql"
                if database_available
                else "memory_fallback" if not database_url else "unavailable"
            ),
            "llm_configured": bool(os.getenv("API_KEY")),
        },
    )


@app.get("/customers", response_model=list[CustomerSummary], tags=["customers"])
def list_customers() -> list[CustomerSummary]:
    return [CustomerSummary.model_validate(item) for item in load_table("customers.json")]


def _order_status(order: dict) -> str:
    if order.get("delivered_date"):
        return "Delivered"
    if order.get("shipped_date"):
        return "Shipped"
    return "PendingShipment"


@app.get(
    "/customers/{customer_id}/orders",
    response_model=list[OrderSummary],
    tags=["customers"],
)
def list_customer_orders(customer_id: str) -> list[OrderSummary]:
    customer = get_customer(customer_id)
    if customer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="当前客户不存在。",
        )

    result = []
    for order in get_orders_by_customer_id(customer_id):
        product = get_product(order.get("product_id")) or {}
        result.append(
            OrderSummary(
                **order,
                product_category=product.get("category"),
                order_status=_order_status(order),
            )
        )
    return result


@app.get(
    "/customers/{customer_id}/orders/{order_id}",
    response_model=OrderDetail,
    tags=["customers"],
)
def get_customer_order_detail(customer_id: str, order_id: str) -> OrderDetail:
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

    product = get_product(order.get("product_id")) or {}
    return OrderDetail(
        **order,
        product_category=product.get("category"),
        order_status=_order_status(order),
        logistics=get_logistics(order_id),
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


@app.post(
    "/service-requests/{request_id}/evidence",
    response_model=EvidenceSummary,
    status_code=status.HTTP_201_CREATED,
    tags=["evidence"],
)
def upload_service_request_evidence(
    request_id: str,
    customer_id: str = Form(...),
    file: UploadFile = File(...),
) -> EvidenceSummary:
    _owned_service_request(request_id, customer_id)
    try:
        record = store_customer_evidence(
            upload=file,
            customer_id=customer_id,
            request_id=request_id,
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
    return EvidenceSummary.model_validate(record)


@app.get(
    "/service-requests/{request_id}/evidence",
    response_model=list[EvidenceSummary],
    tags=["evidence"],
)
def list_service_request_evidence(
    request_id: str,
    customer_id: str,
) -> list[EvidenceSummary]:
    _owned_service_request(request_id, customer_id)
    return [
        EvidenceSummary.model_validate(record)
        for record in list_evidence_records(request_id)
    ]


@app.post(
    "/chat",
    response_model=ChatResponse,
    status_code=status.HTTP_200_OK,
    tags=["chat"],
)
def chat(payload: ChatRequest, request: Request) -> ChatResponse:
    customer = get_customer(payload.customer_id)
    if customer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="当前客户不存在。",
        )

    if payload.order_id:
        order = get_order(payload.order_id)
        if order is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="订单不存在。",
            )
        if order.get("customer_id") != payload.customer_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="该订单不属于当前客户。",
            )

    # A browser-generated conversation ID is not globally trusted. Namespacing
    # it by customer prevents two mock users from sharing the same checkpoint.
    graph_thread_id = f"{payload.customer_id}:{payload.conversation_id}"
    graph_config = {"configurable": {"thread_id": graph_thread_id}}

    try:
        snapshot = request.app.state.conversation_graph.get_state(graph_config)
        existing_context = dict(
            (snapshot.values or {}).get("conversation_context") or {}
        )
        existing_context["authenticated_customer_id"] = payload.customer_id
        if payload.order_id:
            existing_context["active_order_id"] = payload.order_id

        result = request.app.state.conversation_graph.invoke(
            {
                "user_message": payload.message,
                "conversation_context": existing_context,
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
