from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ChatRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    conversation_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
        description="Stable ID used to isolate one conversation thread",
        examples=["customer-001"],
    )
    customer_id: str = Field(
        pattern=r"^CUS\d{4}$",
        description="Authenticated mock customer that owns this conversation",
        examples=["CUS0021"],
    )
    order_id: str | None = Field(
        default=None,
        pattern=r"^ORD\d{10}$",
        description="Optional order context when chat starts from an order page",
        examples=["ORD2026080021"],
    )
    message: str = Field(
        min_length=1,
        max_length=4000,
        examples=["订单ORD2026080021什么时候发货？"],
    )


class ChatResponse(BaseModel):
    conversation_id: str
    message: str
    intent: str | None = None
    order_id: str | None = None
    request_id: str | None = None
    handling_status: str | None = None
    decision: str | None = None
    action: str | None = None
    action_id: str | None = None


class HealthResponse(BaseModel):
    status: str
    service: str
    checks: dict[str, Any]


class CustomerSummary(BaseModel):
    customer_id: str
    vip_level: str
    register_date: str
    refund_history_count: int


class OrderSummary(BaseModel):
    order_id: str
    customer_id: str
    product_id: str
    product_category: str | None = None
    quantity: int
    order_date: str
    shipped_date: str | None = None
    promised_ship_date: str | None = None
    estimated_delivery_date: str | None = None
    delivered_date: str | None = None
    amount: float
    payment_status: str
    order_status: str


class LogisticsSummary(BaseModel):
    carrier: str
    exception_type: str
    exception_recorded_date: str
    resolved: bool


class OrderDetail(OrderSummary):
    logistics: list[LogisticsSummary]


class EvidenceSummary(BaseModel):
    evidence_id: str
    request_id: str
    evidence_type: str
    submitted_by: str
    submitted_date: str
    original_filename: str
    media_type: str
    file_size_bytes: int
    verification_status: str
    verified_facts: list[str]
    verification_confidence: float | None = None
