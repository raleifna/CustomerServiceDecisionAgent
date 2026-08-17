# Order Agent
# 是否错发商品
# 是否少发商品
# 确认少发数量
# 是否签收
# 配送是否逾期
# 申请距离签收天数

from src.backend.graph.state import CustomerServiceState
from datetime import date

def order_agent_node(state: CustomerServiceState,) -> CustomerServiceState:
    order = state.get("order")

    if order is None:
        cur_errors = state.get("errors", [])

        return {
            "errors": cur_errors + ["Order Agent 缺少 order"],
        }
    
    service_request = state.get("service_request")
    if service_request is None:
        cur_errors = state.get("errors", [])

        return {
            "errors": cur_errors + ["Order Agent 缺少 service_request"],
        }

    ordered_product_id = order.get("product_id")
    shipped_product_id = order.get("shipped_product_id")

    if not ordered_product_id:
        cur_errors = state.get("errors", [])

        return {
            "errors": cur_errors + ["Order 缺少 product_id"],
        }

    # product_match: 
    # True → 出库商品一致
    # False → 出库商品不一致
    # None  → 尚未发货 
    if shipped_product_id is None:
        product_match = None
    else:
        product_match = ordered_product_id == shipped_product_id

    ordered_product_qty = order.get("quantity")
    shipped_product_qty= order.get("shipped_quantity")

    if ordered_product_qty is None:
        cur_errors = state.get("errors", [])

        return {
            "errors": cur_errors + ["Order 缺少 quantity"],
        }

    if shipped_product_qty is None:
        qty_match = None
        missing_qty = None
    else:
        qty_match = ordered_product_qty == shipped_product_qty
        missing_qty = max(ordered_product_qty - shipped_product_qty, 0)

    shipped_date_value = order.get("shipped_date")
    delivered_date_value = order.get("delivered_date")
    estimated_delivery_date_value = order.get("estimated_delivery_date")
    request_date_value = service_request.get("request_date")
    delivery_recipient_type = order.get("delivery_recipient_type")

    if not estimated_delivery_date_value:
        cur_errors = state.get("errors", [])

        return {
            "errors": cur_errors + ["Order 缺少 estimated_delivery_date"],
        }

    if not request_date_value:
        cur_errors = state.get("errors", [])

        return {
            "errors": cur_errors + ["Service Request 缺少 request_date"],
        }
    
    request_date = date.fromisoformat(request_date_value)
    estimated_delivery_date = date.fromisoformat(estimated_delivery_date_value)
    if shipped_date_value is None:
        shipped_date = None
    else:
        shipped_date = date.fromisoformat(shipped_date_value)

    if delivered_date_value is None:
        delivered_date = None
    else:
        delivered_date = date.fromisoformat(delivered_date_value)

    is_shipped = shipped_date is not None
    is_delivered = delivered_date is not None
    if not is_shipped:
        delivery_status = "NotShipped"
    elif not is_delivered:
        delivery_status = "InTransit"
    else:
        delivery_status = "Delivered"

    if is_delivered:
        days_since_delivery = (request_date - delivered_date).days

        was_delivered_late = (delivered_date > estimated_delivery_date)  
        delivery_delay_days = max((delivered_date - estimated_delivery_date).days,0,) 

        is_in_transit_overdue = False
    else:
        days_since_delivery = None
        was_delivered_late = False
        is_in_transit_overdue = (
            is_shipped
            and request_date > estimated_delivery_date
        )

        if is_in_transit_overdue:
            delivery_delay_days = (
                request_date - estimated_delivery_date
            ).days
        else:
            delivery_delay_days = 0        
    product = state.get("product")
    customer = state.get("customer")
    if product is None:
        cur_errors = state.get("errors", [])
        return {
            "errors": cur_errors + ["Order Agent 缺少 product"],
        }

    if customer is None:
        cur_errors = state.get("errors", [])
        return {
            "errors": cur_errors + ["Order Agent 缺少 customer"],
        }
    
    amount = order.get("amount")
    payment_status = order.get("payment_status")

    is_fragile = product.get("is_fragile")
    is_customized = product.get("is_customized")

    vip_level = customer.get("vip_level")
    refund_history_count = customer.get("refund_history_count")
    
    return {
        "order_evidence":{
            "order_id": order.get("order_id"),
            "ordered_product_id": ordered_product_id,
            "shipped_product_id": shipped_product_id,
            "product_match": product_match,

            "ordered_quantity": ordered_product_qty,
            "shipped_quantity": shipped_product_qty,
            "quantity_match": qty_match,
            "confirmed_missing_quantity": missing_qty,

            "shipped_date": shipped_date_value,
            "promised_ship_date": order.get("promised_ship_date"),
            "delivered_date": delivered_date_value,
            "estimated_delivery_date": estimated_delivery_date_value,
            "request_date": request_date_value,

            "is_shipped": is_shipped,
            "is_delivered": is_delivered,
            "delivery_status": delivery_status,
            "delivery_recipient_type": delivery_recipient_type,

            "days_since_delivery": days_since_delivery,
            "was_delivered_late": was_delivered_late,
            "is_in_transit_overdue": is_in_transit_overdue,
            "delivery_delay_days": delivery_delay_days,

            "amount": amount,
            "payment_status": payment_status,
            "is_fragile": is_fragile,
            "is_customized": is_customized,
            "vip_level": vip_level,
            "refund_history_count": refund_history_count,

        }
    }
