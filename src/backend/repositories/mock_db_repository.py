import json
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[3]
MOCK_DB_DIR = PROJECT_ROOT / "data" / "mock_db"
# Generated demo test accounts (scripts/demo/seed_test_accounts.py). Kept in
# their own overlay files -- appended after the hand-built fixture rows, in
# a disjoint id namespace (CUS01xx / ORD99...) -- so regenerating them with
# fresh dates never rewrites the evaluation fixtures themselves.
TEST_ACCOUNTS_DIR = MOCK_DB_DIR / "test_accounts"


def _read_table(file_path: Path, filename: str) -> list[dict[str, Any]]:
    with file_path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    if not isinstance(data, list):
        raise ValueError(f"{filename} 的顶层结构必须是 JSON 数组")

    return data


def load_table(filename: str) -> list[dict[str, Any]]:
    data = _read_table(MOCK_DB_DIR / filename, filename)
    overlay = TEST_ACCOUNTS_DIR / filename
    if overlay.is_file():
        data = data + _read_table(overlay, filename)
    return data

def load_test_account_guide() -> dict[str, dict[str, Any]]:
    """customer_id -> {title, hints} for the generated demo test accounts;
    empty when they haven't been generated."""
    guide_file = TEST_ACCOUNTS_DIR / "accounts_guide.json"
    if not guide_file.is_file():
        return {}
    return {row["customer_id"]: row for row in _read_table(guide_file, guide_file.name)}

def get_service_request(request_id: str) -> dict[str, Any] | None:
    service_requests = load_table("service_requests.json")

    for service_request in service_requests:
        if service_request.get("request_id") == request_id:
            return service_request
        
    return None

def get_service_requests_by_order_id(
    order_id: str,
) -> list[dict[str, Any]]:
    service_requests = load_table("service_requests.json")

    return [
        service_request
        for service_request in service_requests
        if service_request.get("order_id") == order_id
    ]

def get_order(order_id: str) -> dict[str, Any] | None:
    orders = load_table("orders.json")

    for order in orders:
        if order.get("order_id") == order_id:
            return order
        
    return None

def get_order_items(order_id: str) -> list[dict[str, Any]]:
    return [
        item for item in load_table("order_items.json")
        if item.get("order_id") == order_id
    ]

def get_order_item(order_item_id: str) -> dict[str, Any] | None:
    for item in load_table("order_items.json"):
        if item.get("order_item_id") == order_item_id:
            return item
    return None

def get_seed_service_request_items(request_id: str) -> list[dict[str, Any]]:
    return [
        item for item in load_table("service_request_items.json")
        if item.get("request_id") == request_id
    ]

def get_orders_by_customer_id(customer_id: str) -> list[dict[str, Any]]:
    orders = load_table("orders.json")

    return [
        order
        for order in orders
        if order.get("customer_id") == customer_id
    ]

def get_customer(customer_id: str) -> dict[str, Any] | None:
    customers = load_table("customers.json")

    for customer in customers:
        if customer.get("customer_id") == customer_id:
            return customer
        
    return None

def get_product(product_id: str) -> dict[str, Any] | None:
    products = load_table("products.json")

    for product in products:
        if product.get("product_id") == product_id:
            return product
        
    return None

def get_logistics(order_id: str) -> list[dict[str, Any]]:
    logistics = load_table("logistics.json")

    matches = []

    for logistic in logistics:
        if logistic.get("order_id") == order_id:
            matches.append(logistic)

    return matches

def get_logistics_tracking(order_id: str) -> dict[str, Any] | None:
    """Routine carrier tracking checkpoints — informational only, not a
    decision-critical source. logistics.json (get_logistics above) stays the
    only thing policy/decision logic reads; this just gives query_agent
    something real to say for "我的快递到哪了" style questions.
    """
    tracking_records = load_table("logistics_tracking.json")

    for record in tracking_records:
        if record.get("order_id") == order_id:
            return record

    return None

def get_evi(request_id: str) -> list[dict[str, Any]]:
    evidence_records = load_table("service_request_evidence.json")

    matches = []

    for evidence_record in evidence_records:
        if evidence_record.get("request_id") == request_id:
            matches.append(evidence_record)

    return matches
