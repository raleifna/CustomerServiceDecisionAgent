"""OrderContextService: composes OrderRepository + ProductRepository +
ShipmentRepository + PaymentRepository into one read-only order context for
Agent Tools to consume, so an Agent Tool calls this ONE service instead of
chaining four repositories itself.

Scope boundary this module exists to enforce -- do not blur it when wiring
callers:
  - Aggregates facts only. Never decides refund eligibility, risk level, or
    which policy/decision rule applies -- that stays Evidence Gate /
    Decision Engine / Policy Agent's job, working from this context's facts.
  - No date.today() anywhere here either. as_of_date is an explicit,
    required parameter threaded straight through to
    ShipmentRepository.get_shipment_facts() -- this is the BusinessClock
    role, satisfied by injection (an Agent Tool is expected to obtain
    as_of_date from BusinessClock and pass it in, not this service).
  - Products are always fetched via ProductRepository.list_by_ids() once,
    batched over every order_item's product_id -- never a per-item
    get_by_id() loop.
  - Distinguishes "the order itself is missing" (OrderContextNotFoundError)
    from "an order_item points at a product that doesn't exist"
    (OrderContextIntegrityError, a genuine data integrity problem) from
    "no shipment/payment record" (NOT an error -- shipment=None or
    payments=[] plus a ContextWarning; never silently reinterpreted as
    "unshipped" or "unpaid"). A repository connection failure is not caught
    anywhere in this module -- it propagates unmodified, exactly like the
    repositories it composes.
  - Only carries a lightweight, static REAL/DERIVED/SYNTHETIC_* summary of
    where each fact group comes from (DataSourceSummary) -- not full
    data_provenance rows. That level of detail is for
    ProductRepository.get_product_provenance() and admin/debug tooling
    directly, not something every Agent Tool call should have to carry.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from src.backend.repositories.order_repository import (
    Order,
    OrderItem,
    OrderNotFoundError,
    OrderRepository,
    get_order_repository,
)
from src.backend.repositories.payment_repository import (
    NoPaymentsForOrderError,
    Payment,
    PaymentFacts,
    PaymentRepository,
    get_payment_repository,
)
from src.backend.repositories.product_repository import (
    Product,
    ProductRepository,
    get_product_repository,
)
from src.backend.repositories.shipment_repository import (
    NoShipmentForOrderError,
    Shipment,
    ShipmentEvent,
    ShipmentFacts,
    ShipmentRepository,
    get_shipment_repository,
)


class OrderContextNotFoundError(LookupError):
    """The order itself doesn't exist. A hard failure -- there is no
    partial context to return."""


class OrderContextIntegrityError(RuntimeError):
    """An order_item references a product_id that ProductRepository can't
    resolve. This is a genuine data integrity problem (not a "this order
    happens to have no shipment yet" situation), so it's raised rather than
    silently producing a context with a hole in it."""

    def __init__(self, order_id: str, missing_product_ids: list[str]):
        self.order_id = order_id
        self.missing_product_ids = missing_product_ids
        super().__init__(
            f"order {order_id!r} has order_items referencing product_id(s) "
            f"that don't exist: {missing_product_ids!r}"
        )


@dataclass(frozen=True)
class ContextWarning:
    code: str          # e.g. 'NO_SHIPMENT_RECORD', 'NO_PAYMENT_RECORDS'
    entity_id: str
    message: str


@dataclass(frozen=True)
class OrderItemContext:
    order_item: OrderItem
    product: Product | None   # None only if ProductRepository legitimately has no row and
    # we did not raise -- in the current design this never happens: a
    # missing product always raises OrderContextIntegrityError before this
    # dataclass is ever constructed. Kept Optional to make that guarantee
    # visible in the type rather than assumed.


@dataclass(frozen=True)
class ConsistencyReport:
    """Technical reconciliation only -- NEVER refund_eligible / risk_level /
    recommended_action / matched_rule. Those stay Decision Engine / Policy
    Agent's job, computed from this report's facts plus others, not
    embedded here."""
    order_items_total: Decimal
    order_total: Decimal
    rounding_adjustment: Decimal
    payment_total: Decimal | None          # None when there are no payment records to sum (see payments=[] case)
    order_amount_reconciled: bool
    payment_amount_reconciled: bool | None  # None when payment_total is None -- not "False" (that would wrongly imply a real mismatch)
    missing_product_ids: list[str]          # always [] on a successfully-returned context; see OrderContextIntegrityError


@dataclass(frozen=True)
class DataSourceSummary:
    """Static, lightweight labels -- not a data_provenance dump. See
    ProductRepository.get_product_provenance() for the full per-field
    provenance trail, kept out of this context on purpose."""
    order_facts: str
    product_assignment: str
    shipment_exception: str
    payment_reference_date: str


@dataclass(frozen=True)
class OrderContext:
    schema_version: str
    order: Order
    items: list[OrderItemContext]
    shipment: Shipment | None
    shipment_events: list[ShipmentEvent]
    shipment_facts: ShipmentFacts | None
    payments: list[Payment]
    payment_facts: PaymentFacts | None
    consistency: ConsistencyReport
    source_summary: DataSourceSummary
    warnings: list[ContextWarning]
    as_of_date: date


_RECONCILE_TOLERANCE = Decimal("0.01")

_POSTGRES_SOURCE_SUMMARY = DataSourceSummary(
    order_facts="REAL (Olist order/shipping dates) + DERIVED (BRL->CNY amount conversion, approximate rate)",
    product_assignment="SYNTHETIC_ENRICHMENT (a real Amazon product constrained-assigned to this Olist "
                        "product within a 0.3x-3.0x real-price band; not the customer's actual original product)",
    shipment_exception="see each event's own source_type (REAL/DERIVED/SYNTHETIC_EVENT) -- "
                        "no exception here asserts a fact the source data didn't actually support",
    payment_reference_date="DERIVED (the order's purchase date reused as a reference point -- "
                            "Olist's payments dataset has no real payment-channel timestamp)",
)

_FIXTURE_SOURCE_SUMMARY = DataSourceSummary(
    order_facts="fixture (hand-authored test data, not sourced from any external dataset)",
    product_assignment="fixture (hand-authored test data)",
    shipment_exception="fixture (hand-authored logistics.json rows, treated as REAL within the fixture)",
    payment_reference_date="N/A (the fixture backend has no payments concept -- see PaymentRepository)",
)


class OrderContextService:
    def __init__(
        self,
        order_repository: OrderRepository,
        product_repository: ProductRepository,
        shipment_repository: ShipmentRepository,
        payment_repository: PaymentRepository,
    ):
        self.order_repository = order_repository
        self._orders = order_repository
        self._products = product_repository
        self._shipments = shipment_repository
        self._payments = payment_repository
        # Determined once, structurally, by which repository classes were
        # injected -- not re-derived per call. Fixture and Postgres are the
        # only two backends this project has; a third backend added later
        # would need its own DataSourceSummary here.
        self._source_summary = (
            _FIXTURE_SOURCE_SUMMARY
            if type(order_repository).__name__ == "FixtureOrderRepository"
            else _POSTGRES_SOURCE_SUMMARY
        )

    def get_order_context(self, order_id: str, *, as_of_date: date) -> OrderContext:
        try:
            order_with_items = self._orders.get_order_with_items(order_id)
        except OrderNotFoundError as exc:
            raise OrderContextNotFoundError(order_id) from exc

        order = order_with_items.order
        items = order_with_items.items
        warnings: list[ContextWarning] = []

        # -- batched product lookup: ONE list_by_ids() call, never a
        # per-item get_by_id() loop --
        product_ids = [item.product_id for item in items]
        products = self._products.list_by_ids(product_ids)
        products_by_id = {p.product_id: p for p in products}

        missing_product_ids = [pid for pid in product_ids if pid not in products_by_id]
        if missing_product_ids:
            raise OrderContextIntegrityError(order_id, missing_product_ids)

        item_contexts = [
            OrderItemContext(order_item=item, product=products_by_id[item.product_id])
            for item in items
        ]

        # -- shipment: missing is a warning, not an error --
        shipment: Shipment | None = None
        shipment_events: list[ShipmentEvent] = []
        shipment_facts: ShipmentFacts | None = None
        try:
            combo = self._shipments.get_shipment_with_events(order_id)
            shipment = combo.shipment
            shipment_events = combo.events
            shipment_facts = self._shipments.get_shipment_facts(order_id, as_of_date=as_of_date)
        except NoShipmentForOrderError:
            warnings.append(ContextWarning(
                code="NO_SHIPMENT_RECORD", entity_id=order_id,
                message="no shipment record for this order -- NOT to be read as 'unshipped', "
                        "just untracked in this backend",
            ))

        # -- payments: missing is a warning, not an error --
        payments: list[Payment] = []
        payment_facts: PaymentFacts | None = None
        try:
            payments = self._payments.list_by_order_id(order_id)
            payment_facts = self._payments.get_payment_facts(order_id)
        except NoPaymentsForOrderError:
            warnings.append(ContextWarning(
                code="NO_PAYMENT_RECORDS", entity_id=order_id,
                message="no payment records for this order -- NOT to be read as 'unpaid', "
                        "just untracked in this backend",
            ))

        consistency = _build_consistency_report(order, items, payments)

        return OrderContext(
            schema_version="v2",
            order=order, items=item_contexts,
            shipment=shipment, shipment_events=shipment_events, shipment_facts=shipment_facts,
            payments=payments, payment_facts=payment_facts,
            consistency=consistency, source_summary=self._source_summary,
            warnings=warnings, as_of_date=as_of_date,
        )


def _build_consistency_report(
    order: Order, items: list[OrderItem], payments: list[Payment]
) -> ConsistencyReport:
    order_items_total = sum((item.paid_amount for item in items), Decimal("0"))
    order_amount_reconciled = abs(
        (order.amount - order.rounding_adjustment) - order_items_total
    ) <= _RECONCILE_TOLERANCE

    if payments:
        payment_total = sum((p.amount for p in payments), Decimal("0"))
        payment_amount_reconciled = abs(payment_total - order.amount) <= _RECONCILE_TOLERANCE
    else:
        payment_total = None
        payment_amount_reconciled = None

    return ConsistencyReport(
        order_items_total=order_items_total, order_total=order.amount,
        rounding_adjustment=order.rounding_adjustment,
        payment_total=payment_total,
        order_amount_reconciled=order_amount_reconciled,
        payment_amount_reconciled=payment_amount_reconciled,
        missing_product_ids=[],  # always empty here -- a non-empty case raises OrderContextIntegrityError instead
    )


def get_order_context_service() -> OrderContextService:
    """Wires up all four repositories via their own APP_DATA_BACKEND-driven
    factories, so a caller doesn't have to construct and match four
    backends by hand. All four read the same APP_DATA_BACKEND env var, so
    they're always consistently Postgres or consistently fixture together."""
    return OrderContextService(
        order_repository=get_order_repository(),
        product_repository=get_product_repository(),
        shipment_repository=get_shipment_repository(),
        payment_repository=get_payment_repository(),
    )
