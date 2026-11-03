"""Order / customer / product display context for the STAFF workbench.

Fixture orders (data/mock_db, ORD\\d{10}) resolve from the JSON fixtures and
the storefront catalog, exactly as before. V2 orders imported from the
Olist + Amazon batch (ORD-RW-xxxxx) exist only in PostgreSQL, so they fall
back to the Postgres repositories here; without that, the workbench showed a
bare order id, a 0 amount and no customer for every V2 case.

The V2 product name comes from data_provenance.metadata.amazon_title -- the
Amazon listing title the import pipeline assigned to the product. Provenance
is admin/debug material (see product_repository.ProductProvenance), which is
why this module lives behind the staff API only and is never used by the
customer-facing storefront.
"""

import logging
from functools import lru_cache
from typing import Any

from src.backend.repositories.mock_db_repository import get_customer, get_order
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


def staff_order(order_id: str) -> dict[str, Any] | None:
    """The order row as a plain dict (fixture JSON shape), from either backend."""
    order = get_order(order_id)
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


def staff_product_card(order_id: str, order: dict[str, Any] | None, order_items: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
    """Display card for the order's (primary) product."""
    if order is not None and order.get("product_id"):
        return get_storefront_product_card(order["product_id"])
    database_url = get_database_url()
    if order is None or not database_url:
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


def staff_customer(customer_id: str) -> dict[str, Any] | None:
    customer = get_customer(customer_id)
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
