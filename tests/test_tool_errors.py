import pytest

from src.backend.services.tool_errors import (
    DATABASE_UNAVAILABLE,
    DATA_INTEGRITY_ERROR,
    ORDER_NOT_FOUND,
    ToolError,
    error_result,
)


def test_to_dict_shape():
    exc = ToolError(ORDER_NOT_FOUND, "No order was found for the supplied order ID.")
    assert exc.to_dict() == {
        "ok": False,
        "error": {
            "code": "ORDER_NOT_FOUND",
            "message": "No order was found for the supplied order ID.",
            "retryable": False,
        },
    }


def test_database_unavailable_defaults_to_retryable():
    exc = ToolError(DATABASE_UNAVAILABLE, "The database is temporarily unavailable.")
    assert exc.retryable is True


def test_non_infra_codes_default_to_not_retryable():
    exc = ToolError(DATA_INTEGRITY_ERROR, "Order data failed an internal consistency check.")
    assert exc.retryable is False


def test_explicit_retryable_overrides_default():
    exc = ToolError(DATABASE_UNAVAILABLE, "msg", retryable=False)
    assert exc.retryable is False


def test_unknown_code_rejected():
    with pytest.raises(ValueError):
        ToolError("NOT_A_REAL_CODE", "msg")


def test_error_result_helper_matches_to_dict():
    assert error_result(ORDER_NOT_FOUND, "not found") == ToolError(ORDER_NOT_FOUND, "not found").to_dict()
