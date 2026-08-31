from src.backend.graph.workflow import (
    load_customer_node,
    load_order_node,
    load_product_node,
)


# --- load_order_node ---

def test_load_order_node_missing_service_request():
    assert "errors" in load_order_node({"service_request": None})


def test_load_order_node_missing_order_id():
    assert "errors" in load_order_node({"service_request": {}})


def test_load_order_node_unknown_order_reports_an_error_not_a_crash():
    result = load_order_node({"service_request": {"order_id": "ORD-DOES-NOT-EXIST-AT-ALL"}})
    assert "errors" in result


def test_load_order_node_populates_order_with_iso_date_strings():
    # ORD2026080021: a real fixture order (never shipped)
    result = load_order_node({"service_request": {"order_id": "ORD2026080021"}})
    order = result["order"]
    assert order["order_id"] == "ORD2026080021"
    assert isinstance(order["amount"], float)
    assert "promised_ship_date" not in order


# --- load_customer_node ---

def test_load_customer_node_missing_service_request():
    assert "errors" in load_customer_node({"service_request": None})


def test_load_customer_node_missing_customer_id():
    assert "errors" in load_customer_node({"service_request": {}})


def test_load_customer_node_unknown_customer_reports_an_error():
    result = load_customer_node({"service_request": {"customer_id": "CUS-DOES-NOT-EXIST"}})
    assert "errors" in result


def test_load_customer_node_populates_customer_with_fixture_refund_history_count():
    result = load_customer_node({"service_request": {"customer_id": "CUS0009"}})
    customer = result["customer"]
    assert customer["customer_id"] == "CUS0009"
    assert customer["refund_history_count"] == 12  # fixture seed value, preserved


# --- load_product_node ---

def test_load_product_node_missing_order_item():
    assert "errors" in load_product_node({"order_item": None})


def test_load_product_node_missing_product_id():
    assert "errors" in load_product_node({"order_item": {}})


def test_load_product_node_unknown_product_reports_an_error():
    result = load_product_node({"order_item": {"product_id": "PRO-DOES-NOT-EXIST"}})
    assert "errors" in result


def test_load_product_node_populates_product():
    result = load_product_node({"order_item": {"product_id": "PRO0009"}})
    product = result["product"]
    assert product["product_id"] == "PRO0009"
    assert isinstance(product["price"], float)
