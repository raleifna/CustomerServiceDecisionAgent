"""The one place the HTTP API reads order / customer / product / logistics data.

Two backends hold business data in this project, on purpose:

- the hand-built fixtures (data/mock_db, ids like ORD2026080001 / CUS0001,
  plus the generated demo accounts) -- the deterministic evaluation baseline,
  with legacy semantics such as promised_ship_date, refund_history_count and
  continuous carrier tracking;
- the V2 Olist + Amazon import in PostgreSQL (ORD-RW-xxxxx / CUS-RW-xxxxx),
  whose schema deliberately models different facts (canceled_order_count,
  shipment events but no tracking feed -- see db/migrations/001 and
  services/query_tool_provider.py).

API modules used to read the fixture JSON directly and patch in Postgres
lookups ad hoc. Now every endpoint asks this facade, which resolves an id
against the fixtures first and falls back to the Postgres repositories, so
"which backend owns this order" is decided here and nowhere else
(enforced by tests/test_api_data_access_boundary.py). The agent graph keeps
its own explicit APP_DATA_BACKEND selection; this module does not change it.

V2 product names come from data_provenance.metadata.amazon_title. Provenance
is admin/debug material (product_repository.ProductProvenance), so
`product_card_for_order` only resolves V2 products for staff-facing callers
(`include_v2=True`); V2 customers cannot use the storefront anyway.
"""

import logging
from functools import lru_cache
from typing import Any

from src.backend.repositories import mock_db_repository
from src.backend.repositories.mock_db_repository import get_customer as _fixture_customer
from src.backend.repositories.mock_db_repository import get_order as _fixture_order
from src.backend.repositories.postgres_repository import get_database_url
from src.backend.repositories.product_catalog_repository import get_storefront_product_card

logger = logging.getLogger(__name__)

# Olist category slug -> (中文名, storefront icon, palette)
V2_CATEGORIES: dict[str, tuple[str, str, str]] = {
    "bed_bath_table": ("床品卫浴", "bed-double", "teal"),
    "health_beauty": ("美妆个护", "flower-2", "rose"),
    "telephony": ("手机通讯", "smartphone", "indigo"),
    "computers_accessories": ("电脑配件", "keyboard", "slate"),
    "electronics": ("数码电子", "headphones", "violet"),
    "toys": ("玩具", "gamepad-2", "orange"),
    "home_appliances": ("家用电器", "fan", "sky"),
    "office_furniture": ("办公家具", "sofa", "amber"),
}


def _iso(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


@lru_cache(maxsize=2048)
def _v2_product_card(database_url: str, product_id: str) -> dict[str, Any] | None:
    """Product rows and their provenance never change after import, so a
    process-wide cache is safe and keeps the history list to one lookup per
    product."""
    from src.backend.repositories.product_repository import (
        PostgresProductRepository,
        ProductNotFoundError,
    )

    repository = PostgresProductRepository(database_url)
    try:
        product = repository.get_by_id(product_id)
        provenance = repository.get_product_provenance(product_id)
    except ProductNotFoundError:
        return None
    title = next(
        (
            record.metadata["amazon_title"]
            for record in provenance.records
            if record.field_name is None and isinstance(record.metadata, dict) and record.metadata.get("amazon_title")
        ),
        None,
    )
    category_name, icon, palette = V2_CATEGORIES.get(product.category, (product.category, "package", "slate"))
    return {
        "product_id": product.product_id,
        "title": title or f"{category_name}商品 {product.product_id}",
        "brand": "V2 导入数据",
        "subtitle": category_name,
        "category": category_name,
        "price": float(product.price),
        "original_price": None,
        "rating": 0,
        "review_count": 0,
        "monthly_sales": 0,
        "tags": ["V2 导入"],
        "icon": icon,
        "palette": palette,
        "is_fragile": product.is_fragile,
        "is_customized": product.is_customized,
    }


def get_order(order_id: str) -> dict[str, Any] | None:
    """The order row as a plain dict (fixture JSON shape), from either backend."""
    order = _fixture_order(order_id)
    if order is not None:
        return order
    database_url = get_database_url()
    if not database_url:
        return None
    from src.backend.repositories.order_repository import OrderNotFoundError, PostgresOrderRepository

    try:
        record = PostgresOrderRepository(database_url).get_by_id(order_id)
    except OrderNotFoundError:
        return None
    except Exception:
        logger.exception("Failed to load V2 order %s for the staff workbench", order_id)
        return None
    return {
        "order_id": record.order_id,
        "customer_id": record.customer_id,
        "order_status": record.order_status,
        "payment_status": record.payment_status,
        "refund_status": record.refund_status,
        "order_date": _iso(record.order_date),
        "shipped_date": _iso(record.shipped_date),
        "delivered_date": _iso(record.delivered_date),
        "estimated_delivery_date": _iso(record.estimated_delivery_date),
        "delivery_recipient_type": record.delivery_recipient_type,
        "amount": float(record.amount),
        "source": "v2",
    }


def product_card_for_order(
    order_id: str,
    order: dict[str, Any] | None,
    order_items: list[dict[str, Any]] | None = None,
    *,
    include_v2: bool = False,
) -> dict[str, Any] | None:
    """Display card for the order's (primary) product. V2 products only with
    include_v2=True (staff-facing callers; see the module docstring)."""
    if order is not None and order.get("product_id"):
        return get_storefront_product_card(order["product_id"])
    database_url = get_database_url()
    if order is None or not database_url or not include_v2:
        return None
    if order_items is None:
        from src.backend.repositories.order_item_repository import get_order_items

        order_items = get_order_items(order_id)
    if not order_items:
        return None
    try:
        return _v2_product_card(database_url, order_items[0]["product_id"])
    except Exception:
        logger.exception("Failed to load V2 product for order %s", order_id)
        return None


def get_customer(customer_id: str) -> dict[str, Any] | None:
    customer = _fixture_customer(customer_id)
    if customer is not None:
        return customer
    database_url = get_database_url()
    if not database_url:
        return None
    from src.backend.repositories.customer_repository import CustomerNotFoundError, PostgresCustomerRepository
    from src.backend.repositories.service_request_repository import get_completed_refund_count

    try:
        record = PostgresCustomerRepository(database_url).get_by_id(customer_id)
    except CustomerNotFoundError:
        return None
    except Exception:
        logger.exception("Failed to load V2 customer %s for the staff workbench", customer_id)
        return None
    return {
        "customer_id": record.customer_id,
        "vip_level": record.vip_level,
        "register_date": _iso(record.register_date),
        # V2 customers carry no refund-history snapshot; count live refunds.
        "refund_history_count": get_completed_refund_count(record.customer_id),
        "canceled_order_count": record.canceled_order_count,
        "source": "v2",
    }


# ---------- storefront / customer-facing reads ----------

def list_customer_orders(customer_id: str) -> list[dict[str, Any]]:
    """A customer's orders, newest first (fixture customers only have
    storefront accounts; V2 customers resolve from Postgres for staff use)."""
    orders = mock_db_repository.get_orders_by_customer_id(customer_id)
    if not orders and get_database_url() and _fixture_customer(customer_id) is None:
        from src.backend.repositories.order_repository import PostgresOrderRepository
        try:
            orders = [
                get_order(order.order_id)
                for order in PostgresOrderRepository(get_database_url()).list_by_customer_id(customer_id)
            ]
        except Exception:
            logger.exception("Failed to list V2 orders for %s", customer_id)
            orders = []
    return sorted((order for order in orders if order), key=lambda order: order["order_date"], reverse=True)


def get_order_items(order_id: str) -> list[dict[str, Any]]:
    from src.backend.repositories.order_item_repository import get_order_items as _items
    return _items(order_id)


def get_logistics_events(order_id: str) -> list[dict[str, Any]]:
    """Carrier exception events (fixture logistics.json). V2 orders have no
    such feed in the API layer yet -- the same documented gap as the agents'
    PostgresQueryToolProvider."""
    return mock_db_repository.get_logistics(order_id)


def get_tracking(order_id: str) -> dict[str, Any] | None:
    """Routine carrier checkpoints (fixture only, see get_logistics_events)."""
    return mock_db_repository.get_logistics_tracking(order_id)


def list_demo_accounts() -> list[dict[str, Any]]:
    """Accounts offered by demo login: the fixture customers plus the
    generated test accounts, each with its scenario guide when it has one."""
    guide = mock_db_repository.load_test_account_guide()
    return [
        {**record, "test_scenario": guide.get(record["customer_id"], {}).get("title"),
         "test_hints": guide.get(record["customer_id"], {}).get("hints", [])}
        for record in mock_db_repository.load_table("customers.json")
    ]


def get_demo_account(customer_id: str) -> dict[str, Any] | None:
    return next((account for account in list_demo_accounts() if account["customer_id"] == customer_id), None)


def fixture_data_available() -> bool:
    return all((mock_db_repository.MOCK_DB_DIR / name).is_file() for name in ("orders.json", "service_requests.json"))


def get_product_facts(product_id: str | None) -> dict[str, Any] | None:
    """Raw catalog facts (category, price, fragile, customized) of a fixture product."""
    return mock_db_repository.get_product(product_id) if product_id else None


def get_service_request_items(request_id: str) -> list[dict[str, Any]]:
    from src.backend.repositories.order_item_repository import get_service_request_items as _items
    return _items(request_id)
