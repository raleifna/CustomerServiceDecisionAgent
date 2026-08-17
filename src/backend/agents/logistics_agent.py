#未解决的运输破损
#未解决的疑似丢件
#曾经滞留，但已经解决
#没有物流异常


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
        
    exception_types = []
    for record in logistics:
        exception_type = record.get("exception_type")
        if exception_type:
            exception_types.append(exception_type)

    unresolved_exception_types = []
    for record in logistics:
        if record.get("resolved") is False:
            exception_type = record.get("exception_type")

            if exception_type:
                unresolved_exception_types.append(exception_type)

    has_logistics_exception = len(logistics) > 0

    has_unresolved_exception = (
        len(unresolved_exception_types) > 0
    )

    has_suspected_loss = (
        "疑似丢件" in exception_types
    )

    has_unresolved_suspected_loss = (
        "疑似丢件" in unresolved_exception_types
    )

    has_shipping_damage = (
        "运输破损" in exception_types
    )

    has_stalled_over_72h = (
        "滞留超72小时" in exception_types
    )

    return {
        "logistics_evidence": {
            "order_id": order.get("order_id"),
            "exception_count": len(logistics),
            "has_logistics_exception": (
                has_logistics_exception
            ),
            "exception_types": exception_types,
            "unresolved_exception_types": (
                unresolved_exception_types
            ),
            "has_unresolved_exception": (
                has_unresolved_exception
            ),
            "has_suspected_loss": has_suspected_loss,
            "has_unresolved_suspected_loss": (
                has_unresolved_suspected_loss
            ),
            "has_shipping_damage": (
                has_shipping_damage
            ),
            "has_stalled_over_72h": (
                has_stalled_over_72h
            ),
        }
    }