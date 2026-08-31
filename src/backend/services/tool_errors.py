"""Unified error taxonomy for the Agent Tool layer.

Every Tool a LangGraph Agent node calls (QueryToolProvider today; whatever
follows it as order_agent.py/logistics_agent.py migrate off
mock_db_repository) must surface failures through exactly one of the codes
below, never a raw psycopg/database exception, a bare stack trace, or an ad
hoc string. Two things this buys:

  - the Agent (and the LLM behind it) can branch on a small, stable
    vocabulary instead of trying to parse arbitrary exception text;
  - `retryable` tells a caller whether trying again might help
    (DATABASE_UNAVAILABLE) versus won't (everything else) -- without the
    Agent needing to know *why*.

Boundary rules callers must respect:
  - The original exception is still the thing that reaches logs/
    observability (tool_calls.error_code stores the code; the traceback
    goes to the process log, never to the Agent/LLM).
  - A missing payment or shipment row is a business fact (see
    PAYMENT_DATA_UNAVAILABLE/SHIPMENT_DATA_UNAVAILABLE below) that must
    never be read as "unpaid" or "unshipped" -- OrderContextService already
    encodes this as a warning, not a hard error; these two codes exist for
    Tools that can't tolerate a partial answer at all and must fail closed
    instead of guessing.
  - DATA_INTEGRITY_ERROR means the data itself is inconsistent (e.g. an
    order_item referencing a product_id that doesn't exist) -- it must
    never be reported as ORDER_NOT_FOUND; the order was found, and that's
    exactly the problem.
"""

from typing import Any

INVALID_ARGUMENT = "INVALID_ARGUMENT"
ORDER_NOT_FOUND = "ORDER_NOT_FOUND"
PRODUCT_NOT_FOUND = "PRODUCT_NOT_FOUND"
PAYMENT_DATA_UNAVAILABLE = "PAYMENT_DATA_UNAVAILABLE"
SHIPMENT_DATA_UNAVAILABLE = "SHIPMENT_DATA_UNAVAILABLE"
DATA_INTEGRITY_ERROR = "DATA_INTEGRITY_ERROR"
DATABASE_UNAVAILABLE = "DATABASE_UNAVAILABLE"
INTERNAL_TOOL_ERROR = "INTERNAL_TOOL_ERROR"

ALL_CODES = frozenset({
    INVALID_ARGUMENT, ORDER_NOT_FOUND, PRODUCT_NOT_FOUND,
    PAYMENT_DATA_UNAVAILABLE, SHIPMENT_DATA_UNAVAILABLE,
    DATA_INTEGRITY_ERROR, DATABASE_UNAVAILABLE, INTERNAL_TOOL_ERROR,
})

# Only a transient/infrastructure failure is retryable. Everything else
# (bad argument, genuinely missing data, a data-integrity break) will fail
# the same way again on retry, so retrying just wastes a round trip.
_RETRYABLE_DEFAULT = {DATABASE_UNAVAILABLE}


class ToolError(Exception):
    """Raised by a Tool/Provider method to signal a classified failure.

    Never raised for "no data found and that's a normal, expected case" --
    that stays a plain `None`/empty-result return (see e.g.
    QueryToolProvider.get_order_status). ToolError is for failures a Tool
    caller must actively branch on: something is broken, unavailable, or
    the caller's own argument was invalid.
    """

    def __init__(self, code: str, message: str, *, retryable: bool | None = None):
        if code not in ALL_CODES:
            raise ValueError(f"unknown tool error code: {code!r}")
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable if retryable is not None else (code in _RETRYABLE_DEFAULT)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "error": {"code": self.code, "message": self.message, "retryable": self.retryable},
        }


def error_result(code: str, message: str, *, retryable: bool | None = None) -> dict[str, Any]:
    """Build the `{"ok": False, "error": {...}}` envelope directly, for call
    sites that want to return a dict rather than raise/catch."""
    return ToolError(code, message, retryable=retryable).to_dict()
