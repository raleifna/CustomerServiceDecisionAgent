import re

from src.backend.graph.state import CustomerServiceState
from src.backend.repositories.service_request_repository import (
    get_service_request,
    get_service_requests_by_order_id,
)


# Two order id shapes, both fixed-width, no wildcard middle section:
#   ORD\d{10}       -- the 27-order fixture (e.g. ORD2026080001)
#   ORD-RW-\d{5}    -- the real V2 Olist/Amazon import batch (e.g. ORD-RW-00001,
#                       see build_olist_sample.py's order_id = f"ORD-RW-{n:05d}")
# The boundary class includes '-' on both sides so a hyphenated id can't be
# torn out of a longer hyphenated token, and so an id can't be matched with
# extra trailing digits/segments silently dropped (a partial-id match here
# would resolve to the wrong order). Deliberately NOT loosened to a generic
# ORD-.* pattern -- a third id shape must be added explicitly, not silently
# accepted by a wildcard.
ORDER_ID_PATTERN = re.compile(
    r"(?<![A-Z0-9-])(?:ORD\d{10}|ORD-RW-\d{5})(?![A-Z0-9-])",
    re.IGNORECASE,
)
# Service request ids are always minted by service_request_repository's
# create_service_request() as 'SR' || YYYYMM || 4-digit sequence (see its
# INSERT), regardless of whether the underlying order came from the fixture
# or the V2 import -- there is no separate V2 request-id shape to add here.
REQUEST_ID_PATTERN = re.compile(
    r"(?<![A-Z0-9])SR\d{10}(?![A-Z0-9])",
    re.IGNORECASE,
)


def request_resolver_node(
    state: CustomerServiceState,
) -> CustomerServiceState:
    """Resolve an existing service request from identifiers in a user message."""
    existing_request_id = state.get("request_id")
    if existing_request_id:
        return {
            "resolver_result": {
                "status": "Resolved",
                "matched_by": "direct_input",
                "request_id": existing_request_id,
            },
        }

    user_message = state.get("user_message", "").strip()
    conversation_result = state.get("conversation_result") or {}
    context = state.get("conversation_context") or {}
    authenticated_customer_id = context.get("authenticated_customer_id")

    request_match = REQUEST_ID_PATTERN.search(user_message)
    contextual_request_id = conversation_result.get("request_id")
    if request_match or contextual_request_id:
        request_id = (
            request_match.group().upper()
            if request_match
            else contextual_request_id
        )
        service_request = get_service_request(request_id)

        if service_request is None:
            return {
                "resolver_result": {
                    "status": "NoExistingRequest",
                    "request_id": request_id,
                },
                "customer_response": "暂未找到该售后申请，请核对申请编号。",
            }

        if (
            authenticated_customer_id
            and service_request.get("customer_id") != authenticated_customer_id
        ):
            return {
                "resolver_result": {
                    "status": "Unauthorized",
                    "request_id": request_id,
                },
                "customer_response": "该售后申请不属于当前账户。",
            }

        return {
            "request_id": request_id,
            "resolver_result": {
                "status": "Resolved",
                "matched_by": "request_id",
                "request_id": request_id,
            },
        }

    order_match = ORDER_ID_PATTERN.search(user_message)
    contextual_order_id = conversation_result.get("order_id")
    if order_match is None and not contextual_order_id:
        return {
            "resolver_result": {
                "status": "MissingOrderId",
            },
            "customer_response": "请提供您的订单号，以便我为您查询售后申请。",
        }

    order_id = (
        order_match.group().upper()
        if order_match
        else contextual_order_id
    )
    matches = get_service_requests_by_order_id(order_id)

    if len(matches) == 1:
        request_id = matches[0]["request_id"]
        return {
            "request_id": request_id,
            "resolver_result": {
                "status": "Resolved",
                "matched_by": "order_id",
                "order_id": order_id,
                "request_id": request_id,
            },
        }

    if not matches:
        return {
            "resolver_result": {
                "status": "NoExistingRequest",
                "order_id": order_id,
            },
            "customer_response": "暂未找到该订单对应的售后申请。",
        }

    return {
        "resolver_result": {
            "status": "MultipleRequests",
            "order_id": order_id,
            "request_ids": [match["request_id"] for match in matches],
        },
        "customer_response": (
            "该订单存在多条售后申请，请提供具体的售后申请编号。"
        ),
    }
