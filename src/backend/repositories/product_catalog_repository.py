"""Storefront display catalog: product titles, brand, SKUs, specs, sample
reviews -- everything the customer-facing shop pages need to render a
product, and nothing the decision workflow reads.

Boundary this module exists to enforce: `product_catalog.json` is pure
PRESENTATION data. Facts that decisions depend on (category, catalog price,
is_fragile, is_customized) are always taken from `products.json` and merged
in here, never authored twice; the service promises shown on a product page
(no-reason return window, fragile-goods inspection notice) are DERIVED from
those facts plus `business_rules.json`, so the storefront can never promise
something Policy Agent would then refuse. Agents and graph nodes must not
import this module -- see tests/test_agent_data_access_boundary.py.

A product that exists in `products.json` but has no catalog entry still
renders, with a generic title built from its category, so a new fixture row
never breaks the shop.
"""

from typing import Any

from src.backend.config.business_rules import load_business_rules
from src.backend.repositories.mock_db_repository import load_table

CATALOG_FILE = "product_catalog.json"

_CATEGORY_DEFAULTS: dict[str, tuple[str, str]] = {
    # category -> (icon key, palette key) for products without a catalog row
    "数码": ("smartphone", "indigo"),
    "家电": ("fan", "sky"),
    "服饰": ("shirt", "teal"),
    "鞋靴": ("footprints", "rose"),
    "食品": ("bean", "orange"),
    "家居": ("sofa", "amber"),
    "奢侈品": ("gem", "rose"),
    "饰品": ("gem", "violet"),
}


def _catalog_by_id() -> dict[str, dict[str, Any]]:
    return {row["product_id"]: row for row in load_table(CATALOG_FILE)}


def _service_promises(fact: dict[str, Any]) -> list[dict[str, str]]:
    no_reason_days = load_business_rules()["refund"]["no_reason_return_days"]
    promises = [{"title": "正品保障", "detail": "平台自营及授权商家，假一赔十"}]
    if fact.get("is_customized"):
        promises.append({
            "title": "定制商品",
            "detail": "不支持无理由退货；质量问题、错发、少发仍可申请售后",
        })
    else:
        promises.append({
            "title": f"{no_reason_days}天无理由退货",
            "detail": f"签收后 {no_reason_days} 天内，商品完好可申请无理由退货",
        })
    if fact.get("is_fragile"):
        promises.append({
            "title": "易碎品保障",
            "detail": "签收时请当面验货，破损请拍照并联系客服",
        })
    promises.append({"title": "极速退款", "detail": "智能客服审核通过后原路退回"})
    return promises


def _card(fact: dict[str, Any], display: dict[str, Any] | None) -> dict[str, Any]:
    category = fact.get("category") or "其他"
    icon, palette = _CATEGORY_DEFAULTS.get(category, ("package", "slate"))
    display = display or {}
    return {
        "product_id": fact["product_id"],
        "title": display.get("title") or f"{category}精选商品 {fact['product_id']}",
        "brand": display.get("brand") or "ShopCare 优选",
        "subtitle": display.get("subtitle") or "",
        "category": category,
        "price": float(fact["price"]),
        "original_price": display.get("original_price"),
        "rating": display.get("rating", 4.5),
        "review_count": display.get("review_count", 0),
        "monthly_sales": display.get("monthly_sales", 0),
        "tags": display.get("tags", []),
        "icon": display.get("icon", icon),
        "palette": display.get("palette", palette),
        "is_fragile": bool(fact.get("is_fragile")),
        "is_customized": bool(fact.get("is_customized")),
    }


def list_storefront_products(
    *,
    category: str | None = None,
    query: str | None = None,
    sort: str | None = None,
) -> list[dict[str, Any]]:
    catalog = _catalog_by_id()
    cards = [_card(fact, catalog.get(fact["product_id"])) for fact in load_table("products.json")]
    if category:
        cards = [card for card in cards if card["category"] == category]
    if query:
        needle = query.strip().lower()
        cards = [
            card for card in cards
            if needle in " ".join(
                (card["title"], card["brand"], card["subtitle"], card["category"], card["product_id"])
            ).lower()
        ]
    if sort == "price_asc":
        cards.sort(key=lambda card: card["price"])
    elif sort == "price_desc":
        cards.sort(key=lambda card: card["price"], reverse=True)
    elif sort == "sales":
        cards.sort(key=lambda card: card["monthly_sales"], reverse=True)
    elif sort == "rating":
        cards.sort(key=lambda card: (card["rating"], card["review_count"]), reverse=True)
    return cards


def list_storefront_categories() -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for fact in load_table("products.json"):
        category = fact.get("category") or "其他"
        counts[category] = counts.get(category, 0) + 1
    return [{"category": name, "product_count": count} for name, count in counts.items()]


def get_storefront_product_card(product_id: str | None) -> dict[str, Any] | None:
    if not product_id:
        return None
    for fact in load_table("products.json"):
        if fact.get("product_id") == product_id:
            return _card(fact, _catalog_by_id().get(product_id))
    return None


def get_storefront_product_detail(product_id: str) -> dict[str, Any] | None:
    card = get_storefront_product_card(product_id)
    if card is None:
        return None
    fact = next(row for row in load_table("products.json") if row.get("product_id") == product_id)
    display = _catalog_by_id().get(product_id) or {}
    return {
        **card,
        "highlights": display.get("highlights", []),
        "description": display.get("description", ""),
        "specs": display.get("specs", []),
        "skus": display.get("skus") or [{"sku_id": f"{product_id}-DEFAULT", "sku_name": "默认规格"}],
        "reviews": display.get("reviews", []),
        "service_promises": _service_promises(fact),
    }
