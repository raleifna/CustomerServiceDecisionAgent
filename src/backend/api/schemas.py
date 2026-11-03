from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ChatRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    conversation_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
        description="Stable ID used to isolate one conversation thread",
        examples=["customer-001"],
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
    request_reason: str | None = None
    handling_status: str | None = None
    decision: str | None = None
    action: str | None = None
    action_id: str | None = None


class HealthResponse(BaseModel):
    status: str
    service: str
    checks: dict[str, Any]


class ProductCard(BaseModel):
    """Storefront display data -- presentation only, never decision input."""
    product_id: str
    title: str
    brand: str
    subtitle: str = ""
    category: str
    price: float
    original_price: float | None = None
    rating: float
    review_count: int
    monthly_sales: int
    tags: list[str] = Field(default_factory=list)
    icon: str
    palette: str
    is_fragile: bool = False
    is_customized: bool = False


class ProductSpec(BaseModel):
    label: str
    value: str


class ProductSku(BaseModel):
    sku_id: str
    sku_name: str


class ProductReview(BaseModel):
    user: str
    rating: int
    date: str
    sku_name: str
    content: str


class ServicePromise(BaseModel):
    title: str
    detail: str


class ProductDetail(ProductCard):
    highlights: list[str] = Field(default_factory=list)
    description: str = ""
    specs: list[ProductSpec] = Field(default_factory=list)
    skus: list[ProductSku] = Field(default_factory=list)
    reviews: list[ProductReview] = Field(default_factory=list)
    service_promises: list[ServicePromise] = Field(default_factory=list)


class ProductCategory(BaseModel):
    category: str
    product_count: int


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
    product: ProductCard | None = None


class LogisticsSummary(BaseModel):
    carrier: str
    exception_type: str
    exception_recorded_date: str
    resolved: bool


class OrderItemSummary(BaseModel):
    order_item_id: str
    order_id: str
    product_id: str
    sku_id: str
    sku_name: str
    unit_price: float
    quantity: int
    discount_amount: float
    paid_amount: float
    shipped_product_id: str | None = None
    shipped_quantity: int | None = None


class TrackingCheckpoint(BaseModel):
    time: str
    location: str
    description: str


class OrderTracking(BaseModel):
    order_id: str
    carrier: str | None = None
    tracking_number: str | None = None
    current_status: str | None = None
    checkpoints: list[TrackingCheckpoint] = Field(default_factory=list)


class OrderDetail(OrderSummary):
    logistics: list[LogisticsSummary]
    order_items: list[OrderItemSummary] = Field(default_factory=list)
    refunded_amount: float = 0.0
    tracking: OrderTracking | None = None


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
    customer_description: str | None = None
    file_url: str = ""
    review_notes: str | None = None


class CustomerEvidenceReview(BaseModel):
    evidence_id: str
    verification_status: str
    review_notes: str | None = None
    reviewed_at: str | None = None


class CustomerServiceRequestStatus(BaseModel):
    request_id: str
    workflow_status: str
    decision: str | None = None
    approved_amount: float | None = None
    resolution_notes: str | None = None
    resolved_at: str | None = None
    return_order: dict[str, Any] | None = None
    evidence_reviews: list[CustomerEvidenceReview] = Field(default_factory=list)
    actions: list[dict[str, Any]] = Field(default_factory=list)


class CustomerServiceRequestProgress(BaseModel):
    request_id: str
    order_id: str
    request_type: str
    request_reason: str
    requested_quantity: int
    request_date: str
    workflow_status: str
    decision: str | None = None
    approved_quantity: int | None = None
    approved_amount: float | None = None
    resolution_notes: str | None = None
    resolved_at: str | None = None
    evidence_reviews: list[CustomerEvidenceReview] = Field(default_factory=list)
    evidence: list[EvidenceSummary] = Field(default_factory=list)
    actions: list[dict[str, Any]] = Field(default_factory=list)
    return_order: dict[str, Any] | None = None


class ReturnShipmentRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    carrier: str = Field(min_length=1, max_length=100)
    tracking_number: str = Field(min_length=6, max_length=40, pattern=r"^[A-Za-z0-9-]+$")


class ReturnInspectionRequest(BaseModel):
    accepted: bool
    notes: str | None = Field(default=None, max_length=2000)


class StaffServiceRequestSummary(BaseModel):
    request_id: str
    order_id: str
    customer_id: str
    request_type: str
    request_reason: str
    target_order_item_id: str | None = None
    requested_quantity: int
    request_date: str
    workflow_status: str
    decision: str | None = None
    reasons: list[str] = Field(default_factory=list)
    rule_hits: list[str] = Field(default_factory=list)
    required_actions: list[str] = Field(default_factory=list)
    assigned_to: str | None = None
    lease_expires_at: str | None = None
    row_version: int = 0
    approved_quantity: int | None = None
    approved_amount: float | None = None
    decision_confidence: float | None = None
    resolved_by: str | None = None
    resolution_notes: str | None = None
    resolved_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    # Storefront display card for the order's product (presentation only).
    product: ProductCard | None = None
    order_amount: float | None = None


class StaffEvidenceSummary(BaseModel):
    evidence_id: str
    request_id: str
    original_filename: str
    media_type: str
    verification_status: str
    submitted_date: str
    file_url: str
    customer_description: str | None = None
    review_notes: str | None = None
    reviewed_by: str | None = None
    reviewed_at: str | None = None


class StaffQueueResponse(BaseModel):
    service_requests: list[StaffServiceRequestSummary]
    evidence: list[StaffEvidenceSummary]
    failed_actions: list["StaffActionSummary"] = Field(default_factory=list)


class StaffActionSummary(BaseModel):
    action_id: str
    request_id: str
    action_type: str
    workflow_status: str
    attempts: int
    max_attempts: int
    last_error: str | None = None
    external_transaction_id: str | None = None


class StaffServiceRequestDetail(BaseModel):
    service_request: StaffServiceRequestSummary
    order: dict[str, Any] | None = None
    customer: dict[str, Any] | None = None
    product: dict[str, Any] | None = None
    logistics: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[StaffEvidenceSummary] = Field(default_factory=list)
    order_items: list[OrderItemSummary] = Field(default_factory=list)
    request_items: list[dict[str, Any]] = Field(default_factory=list)
    tracking: OrderTracking | None = None
    return_order: dict[str, Any] | None = None
    actions: list[dict[str, Any]] = Field(default_factory=list)
    # The same customer's other after-sales cases, newest first.
    customer_requests: list[StaffServiceRequestSummary] = Field(default_factory=list)


class StaffReturnOrder(BaseModel):
    return_order_id: str
    request_id: str
    order_id: str
    customer_id: str
    order_item_id: str
    quantity: int
    refundable_amount: float
    workflow_status: str
    return_carrier: str | None = None
    return_tracking_number: str | None = None
    shipped_at: str | None = None
    received_at: str | None = None
    inspected_at: str | None = None
    inspection_result: str | None = None
    inspection_notes: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    request_reason: str | None = None
    product: ProductCard | None = None


class ServiceRequestResolutionRequest(BaseModel):
    decision: str = Field(pattern=r"^(Approve|Reject|PartialApprove|NoActionRequired)$")
    approved_quantity: int | None = Field(default=None, ge=0)
    approved_amount: float | None = Field(default=None, ge=0)
    expected_version: int = Field(ge=0)
    resolution_notes: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def require_closure_reason(self):
        if self.decision in {"Reject", "NoActionRequired"} and not (
            self.resolution_notes and self.resolution_notes.strip()
        ):
            raise ValueError("关闭或拒绝申请时必须填写处理依据")
        return self


class ServiceRequestClaimRequest(BaseModel):
    lease_minutes: int = Field(default=15, ge=5, le=60)


class EvidenceReviewRequest(BaseModel):
    verification_status: str = Field(pattern=r"^(Verified|Rejected)$")
    review_notes: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def require_rejection_reason(self):
        if self.verification_status == "Rejected" and not (
            self.review_notes and self.review_notes.strip()
        ):
            raise ValueError("驳回证据时必须填写驳回原因")
        return self


class EvidenceReviewResponse(BaseModel):
    evidence: StaffEvidenceSummary
    requeued_decision: bool
