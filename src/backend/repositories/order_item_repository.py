from decimal import Decimal
from typing import Any

from src.backend.repositories.mock_db_repository import (
    get_order_item as get_seed_order_item,
    get_order_items as get_seed_order_items,
    get_seed_service_request_items,
)
from src.backend.repositories.postgres_repository import get_database_url


def _normalize(item: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(item)
    for field in ("unit_price", "discount_amount", "paid_amount", "approved_amount"):
        if isinstance(normalized.get(field), Decimal):
            normalized[field] = float(normalized[field])
    return normalized


def get_order_items(order_id: str) -> list[dict[str, Any]]:
    database_url = get_database_url()
    if database_url:
        import psycopg
        from psycopg.rows import dict_row
        with psycopg.connect(database_url, row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT * FROM order_items WHERE order_id = %s ORDER BY order_item_id",
                    (order_id,),
                )
                records = cursor.fetchall()
                if records:
                    return [_normalize(dict(record)) for record in records]
    return [_normalize(item) for item in get_seed_order_items(order_id)]


def get_order_item(order_item_id: str) -> dict[str, Any] | None:
    database_url = get_database_url()
    if database_url:
        import psycopg
        from psycopg.rows import dict_row
        with psycopg.connect(database_url, row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT * FROM order_items WHERE order_item_id = %s",
                    (order_item_id,),
                )
                record = cursor.fetchone()
                if record:
                    return _normalize(dict(record))
    seed = get_seed_order_item(order_item_id)
    return _normalize(seed) if seed else None


def get_service_request_items(request_id: str) -> list[dict[str, Any]]:
    relations = list(get_seed_service_request_items(request_id))
    database_url = get_database_url()
    if database_url:
        import psycopg
        from psycopg.rows import dict_row
        with psycopg.connect(database_url, row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT request_item_id, request_id, order_item_id,
                              requested_quantity, approved_quantity, approved_amount
                       FROM service_request_items WHERE request_id = %s
                       ORDER BY request_item_id""",
                    (request_id,),
                )
                db_relations = [_normalize(dict(record)) for record in cursor.fetchall()]
                if db_relations:
                    relations = db_relations
    return relations
