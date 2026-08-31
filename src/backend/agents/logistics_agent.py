# 未解决的物流停滞
# 曾经滞留，但已经解决
# 没有物流异常
#
# state["logistics"] is LogisticsFacts.as_dict() (see
# src/backend/services/logistics_service.py) -- every boolean-shaped fact
# here is a {availability, value, reason, source_type} signal, not a plain
# bool. This node mostly passes those signals through into logistics_evidence
# unchanged (policy_agent.py is what branches on availability), plus derives
# the exception_types/unresolved_exception_types display lists from the raw
# exceptions list, which is safe to do on either backend -- it's just
# re-grouping records logistics_service.py already fetched.

from src.backend.graph.state import CustomerServiceState


def logistics_agent_node(state: CustomerServiceState,) -> CustomerServiceState:
    logistics = state.get("logistics")

    if logistics is None:
        cur_errors = state.get("errors", [])

        return {
            "errors": cur_errors + ["Logistics Agent 缺少 logistics"],
        }

    order = state.get("order")

    if order is None:
        cur_errors = state.get("errors", [])

        return {
            "errors": cur_errors + ["Logistics Agent 缺少 order"],
        }

    exceptions = logistics.get("exceptions", [])
    exception_types = [e["exception_type"] for e in exceptions]
    unresolved_exception_types = [
        e["exception_type"] for e in exceptions if e.get("resolved") is False
    ]

    return {
        "logistics_evidence": {
            "order_id": order.get("order_id"),
            "exception_count": len(exceptions),
            "exception_types": exception_types,
            "unresolved_exception_types": unresolved_exception_types,
            # Passed through as signals, not plain bools -- see
            # logistics_service.py's module docstring. policy_agent.py must
            # branch on availability before trusting value; value is None
            # (never coerced to False) whenever availability == UNAVAILABLE.
            "has_logistics_exception": logistics["has_logistics_exception"],
            "has_unresolved_exception": logistics["has_unresolved_exception"],
            "delivery_overdue": logistics["delivery_overdue"],
            "tracking_stalled_over_72h": logistics["tracking_stalled_over_72h"],
            "unresolved_tracking_stalled_over_72h": logistics["unresolved_tracking_stalled_over_72h"],
            "stalled_in_transit_over_72h": logistics["stalled_in_transit_over_72h"],
        }
    }
