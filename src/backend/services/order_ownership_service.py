"""OrderOwnershipService: the sole authority for "does this order belong to
this authenticated customer" -- a security check run deterministically
every conversational turn that carries an explicit order_id, never a
general-purpose Tool an LLM decides whether to call.

Unlike QueryToolProvider (genuinely different capabilities per backend --
Postgres/V2 data plainly lacks promised_ship_date/continuous tracking),
ownership verification needs exactly one fact both backends already
provide identically: does an order with this id exist, and does its
customer_id match. OrderRepository (Fixture and Postgres) already exposes
that uniformly via get_by_id(), so this is ONE service class parameterized
by an injected OrderRepository, not two provider classes duplicating the
same three-line comparison -- get_order_ownership_service() picks which
OrderRepository backend to inject, same APP_DATA_BACKEND-driven, no
implicit fallback, contract as every other factory in this project.

verify_ownership() returns only AUTHORIZED/DENIED for a request that could
actually be answered. ORDER_NOT_FOUND, DATABASE_UNAVAILABLE, and
DATA_INTEGRITY_ERROR are raised as ToolError instead of folded into
DENIED -- an infrastructure failure must fail closed (never let the caller
render order data) without asserting the specific, false claim "this order
belongs to someone else", which a database outage does not actually
support. It is the caller's job (see conversation_router_agent.py) to
decide what the customer sees for each case -- this service only reports
what happened.
"""

from dataclasses import dataclass
from typing import Protocol

from src.backend.repositories.order_repository import (
    Order,
    OrderNotFoundError,
    OrderRepository,
    get_order_repository,
)
from src.backend.services.tool_errors import (
    DATABASE_UNAVAILABLE,
    DATA_INTEGRITY_ERROR,
    ORDER_NOT_FOUND,
    ToolError,
)

AUTHORIZED = "AUTHORIZED"
DENIED = "DENIED"


@dataclass(frozen=True)
class OwnershipResult:
    status: str  # AUTHORIZED | DENIED


class OrderOwnershipServiceProtocol(Protocol):
    def verify_ownership(self, order_id: str, customer_id: str) -> OwnershipResult: ...


class OrderOwnershipService:
    def __init__(self, order_repository: OrderRepository):
        self._order_repository = order_repository

    def verify_ownership(self, order_id: str, customer_id: str) -> OwnershipResult:
        import psycopg

        try:
            order: Order = self._order_repository.get_by_id(order_id)
        except OrderNotFoundError as exc:
            raise ToolError(
                ORDER_NOT_FOUND, "No order was found for the supplied order ID.",
            ) from exc
        except psycopg.OperationalError as exc:
            raise ToolError(
                DATABASE_UNAVAILABLE, "The database is temporarily unavailable.", retryable=True,
            ) from exc

        # A NOT NULL constraint on orders.customer_id should make this
        # unreachable in practice; treated as a data problem rather than a
        # denial if it ever isn't, since "" != customer_id would otherwise
        # silently read as an ordinary DENIED with no trace of why.
        if not order.customer_id:
            raise ToolError(
                DATA_INTEGRITY_ERROR, "Order record is missing an owning customer_id.",
            )

        if order.customer_id != customer_id:
            return OwnershipResult(DENIED)
        return OwnershipResult(AUTHORIZED)


def get_order_ownership_service() -> OrderOwnershipServiceProtocol:
    return OrderOwnershipService(get_order_repository())
