"""QueryToolProvider: the single data-access boundary for query_agent.py's
Tool layer. query_agent_node never imports mock_db_repository, a
Repository, or OrderContextService directly -- it only talks to whichever
provider get_query_tool_provider() hands it, selected the same way as every
other backend in this project: APP_DATA_BACKEND=fixture|postgres.

Two providers, two genuinely different capability sets -- not two
implementations of the same capability:

  FixtureQueryToolProvider   full legacy semantics (promised_ship_date,
                              continuous carrier tracking checkpoints) --
                              used by the 27-order fixture and all 34
                              deterministic eval cases.
  PostgresQueryToolProvider  the real Olist/Amazon V2 data. Neither
                              promised_ship_date nor a continuous tracking
                              feed exists in that source data, so those
                              capabilities report availability='UNAVAILABLE'
                              with a reason, rather than being silently
                              omitted or faked. See the module-level
                              CAPABILITY dict for exact reason codes.

`can_offer` on the expedite/logistics_chase blocks is `None`, never
`False`, when the underlying capability is unavailable -- `False` would
claim "we checked and the answer is no", which is not true; `None` means
"we don't have the fact needed to answer at all". Callers (query_agent.py)
must branch on availability before trusting can_offer, not treat None as
falsy.
"""

from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

from src.backend.repositories import mock_db_repository
from src.backend.services.order_context_service import (
    OrderContextNotFoundError,
    OrderContextService,
    get_order_context_service,
)

AVAILABLE = "AVAILABLE"
UNAVAILABLE = "UNAVAILABLE"

REASON_PROMISED_SHIP_DATE_NOT_PROVIDED = "PROMISED_SHIP_DATE_NOT_PROVIDED"
REASON_SOURCE_DATA_NOT_PROVIDED = "SOURCE_DATA_NOT_PROVIDED"


@dataclass(frozen=True)
class CustomerOrderSummary:
    order_id: str
    status_label: str  # '已签收' | '运输中' | '待发货' -- for the "you have N orders, pick one" prompt


class QueryToolProvider(Protocol):
    def list_orders_for_customer(self, customer_id: str) -> list[CustomerOrderSummary]: ...
    def get_order_status(self, order_id: str, *, as_of_date: date) -> dict[str, Any] | None: ...
    def get_logistics_status(self, order_id: str, *, as_of_date: date) -> dict[str, Any]: ...


class FixtureQueryToolProvider:
    """Wraps the existing JSON fixture exactly as query_agent.py already
    read it -- every field the pre-migration code returned is still here,
    unchanged, plus the new capability wrapper (always AVAILABLE, since the
    fixture genuinely has this data)."""

    def list_orders_for_customer(self, customer_id: str) -> list[CustomerOrderSummary]:
        orders = mock_db_repository.get_orders_by_customer_id(customer_id)
        summaries = []
        for order in orders:
            if order.get("delivered_date"):
                label = "已签收"
            elif order.get("shipped_date"):
                label = "运输中"
            else:
                label = "待发货"
            summaries.append(CustomerOrderSummary(order_id=order["order_id"], status_label=label))
        return summaries

    def get_order_status(self, order_id: str, *, as_of_date: date) -> dict[str, Any] | None:
        order = mock_db_repository.get_order(order_id)
        if order is None:
            return None

        shipped_date = order.get("shipped_date")
        delivered_date = order.get("delivered_date")
        promised_ship_date = order.get("promised_ship_date")
        as_of_iso = as_of_date.isoformat()

        if promised_ship_date:
            can_offer_expedite = not shipped_date
            is_overdue = bool(can_offer_expedite and promised_ship_date < as_of_iso)
            expedite = {
                "availability": AVAILABLE, "reason": None,
                "promised_ship_date": promised_ship_date, "can_offer": can_offer_expedite,
            }
        else:
            is_overdue = False
            expedite = {
                "availability": UNAVAILABLE, "reason": REASON_PROMISED_SHIP_DATE_NOT_PROVIDED,
                "promised_ship_date": None, "can_offer": None,
            }

        return {
            "order_id": order_id,
            "payment_status": order.get("payment_status"),
            "shipped_date": shipped_date,
            "delivered_date": delivered_date,
            "estimated_delivery_date": order.get("estimated_delivery_date"),
            "is_shipped": bool(shipped_date),
            "is_delivered": bool(delivered_date),
            "is_overdue": is_overdue,
            "expedite": expedite,
        }

    def get_logistics_status(self, order_id: str, *, as_of_date: date) -> dict[str, Any]:
        order = mock_db_repository.get_order(order_id) or {}
        logistics = mock_db_repository.get_logistics(order_id)
        tracking = mock_db_repository.get_logistics_tracking(order_id)

        shipped_date = order.get("shipped_date")
        delivered_date = order.get("delivered_date")
        estimated_delivery_date = order.get("estimated_delivery_date")
        as_of_iso = as_of_date.isoformat()

        if delivered_date:
            shipment_status = "Delivered"
        elif shipped_date:
            shipment_status = "InTransit"
        else:
            shipment_status = "Pending"

        is_overdue = bool(
            shipped_date and not delivered_date
            and estimated_delivery_date and estimated_delivery_date < as_of_iso
        )
        if is_overdue:
            shipment_status = "Overdue"
        delay_days = (
            (date.fromisoformat(as_of_iso) - date.fromisoformat(estimated_delivery_date)).days
            if is_overdue else None
        )

        checkpoints = tracking.get("checkpoints") if tracking else []
        current_location = checkpoints[-1].get("location") if checkpoints else None

        unresolved_stall = any(
            not item.get("resolved")
            and any(k in str(item.get("exception_type") or "") for k in ("停滞", "滞留", "长期未更新"))
            for item in logistics
        )
        days_stale = None
        if checkpoints:
            latest_date_text = checkpoints[-1].get("time", "").split(" ")[0]
            if latest_date_text and latest_date_text < as_of_iso:
                days_stale = (date.fromisoformat(as_of_iso) - date.fromisoformat(latest_date_text)).days
        can_offer_chase = bool(
            shipped_date and not delivered_date
            and (is_overdue or any(not i.get("resolved") for i in logistics)
                 or (days_stale is not None and days_stale >= 2))
        )

        return {
            "order_id": order_id,
            "shipment_status": shipment_status,
            "shipped_at": shipped_date,
            "delivered_at": delivered_date,
            "estimated_delivery_at": estimated_delivery_date,
            "is_shipped": bool(shipped_date),
            "is_delivered": bool(delivered_date),
            "is_overdue": is_overdue,
            "delay_days": delay_days,
            "exceptions": [
                {
                    "carrier": item.get("carrier"), "exception_type": item.get("exception_type"),
                    "exception_recorded_date": item.get("exception_recorded_date"),
                    "resolved": item.get("resolved"),
                }
                for item in logistics
            ],
            "unresolved_stall": unresolved_stall,
            "tracking": {
                "availability": AVAILABLE, "reason": None,
                "carrier": tracking.get("carrier") if tracking else None,
                "tracking_number": tracking.get("tracking_number") if tracking else None,
                "current_location": current_location,
                "checkpoints": checkpoints or [],
            },
            "logistics_chase": {"availability": AVAILABLE, "can_offer": can_offer_chase},
        }


class PostgresQueryToolProvider:
    """Wraps OrderContextService -- the real V2 data. tracking and expedite
    both report UNAVAILABLE with a reason: promised_ship_date and a
    continuous carrier tracking feed are not concepts this data source
    provides at all (see the scope-conflict note this module's authors
    resolved before writing this class), not values we forgot to fill in."""

    def __init__(self, context_service: OrderContextService):
        self._context_service = context_service

    def list_orders_for_customer(self, customer_id: str) -> list[CustomerOrderSummary]:
        orders = self._context_service.order_repository.list_by_customer_id(customer_id)
        summaries = []
        for order in orders:
            if order.delivered_date:
                label = "已签收"
            elif order.shipped_date:
                label = "运输中"
            else:
                label = "待发货"
            summaries.append(CustomerOrderSummary(order_id=order.order_id, status_label=label))
        return summaries

    def get_order_status(self, order_id: str, *, as_of_date: date) -> dict[str, Any] | None:
        try:
            ctx = self._context_service.get_order_context(order_id, as_of_date=as_of_date)
        except OrderContextNotFoundError:
            return None
        order = ctx.order

        return {
            "order_id": order_id,
            "payment_status": order.payment_status,
            "shipped_date": order.shipped_date.isoformat() if order.shipped_date else None,
            "delivered_date": order.delivered_date.isoformat() if order.delivered_date else None,
            "estimated_delivery_date": (
                order.estimated_delivery_date.isoformat() if order.estimated_delivery_date else None
            ),
            "is_shipped": ctx.shipment_facts.is_shipped if ctx.shipment_facts else bool(order.shipped_date),
            "is_delivered": ctx.shipment_facts.is_delivered if ctx.shipment_facts else bool(order.delivered_date),
            "is_overdue": False,  # order-level "shipping overdue" needs promised_ship_date -- not provided; see expedite block
            "expedite": {
                "availability": UNAVAILABLE, "reason": REASON_PROMISED_SHIP_DATE_NOT_PROVIDED,
                "promised_ship_date": None, "can_offer": None,
            },
        }

    def get_logistics_status(self, order_id: str, *, as_of_date: date) -> dict[str, Any]:
        ctx = self._context_service.get_order_context(order_id, as_of_date=as_of_date)
        facts = ctx.shipment_facts
        shipment = ctx.shipment

        return {
            "order_id": order_id,
            "shipment_status": facts.shipment_status if facts else (shipment.shipment_status if shipment else "Pending"),
            "shipped_at": facts.shipped_at.isoformat() if facts and facts.shipped_at else None,
            "delivered_at": facts.delivered_at.isoformat() if facts and facts.delivered_at else None,
            "estimated_delivery_at": (
                facts.estimated_delivery_at.isoformat() if facts and facts.estimated_delivery_at else None
            ),
            "is_shipped": facts.is_shipped if facts else False,
            "is_delivered": facts.is_delivered if facts else False,
            "is_overdue": facts.is_overdue if facts else False,
            "delay_days": facts.delay_days if facts else None,
            "exceptions": [
                {
                    "carrier": shipment.carrier if shipment else None,
                    "exception_type": e.event_type, "exception_recorded_date": e.event_time.isoformat(),
                    "resolved": e.resolved,
                }
                for e in ctx.shipment_events
            ],
            "unresolved_stall": (facts.unresolved_exception_count > 0) if facts else False,
            "tracking": {
                "availability": UNAVAILABLE, "reason": REASON_SOURCE_DATA_NOT_PROVIDED,
                "carrier": None, "tracking_number": None, "current_location": None, "checkpoints": [],
            },
            "logistics_chase": {
                "availability": AVAILABLE,
                "can_offer": bool(facts and facts.is_shipped and not facts.is_delivered
                                   and (facts.is_overdue or facts.unresolved_exception_count > 0)),
            },
        }


def get_query_tool_provider() -> QueryToolProvider:
    import os
    backend = os.getenv("APP_DATA_BACKEND", "fixture").strip().lower()
    if backend == "postgres":
        return PostgresQueryToolProvider(get_order_context_service())
    if backend == "fixture":
        return FixtureQueryToolProvider()
    raise RuntimeError(f"unknown APP_DATA_BACKEND={backend!r}, expected 'postgres' or 'fixture'")
