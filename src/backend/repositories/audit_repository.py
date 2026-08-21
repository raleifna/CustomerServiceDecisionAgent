from typing import Any
from uuid import uuid4

from src.backend.repositories.postgres_repository import get_database_url


def write_audit_log(
    *, actor_id: str, actor_role: str, action: str,
    resource_type: str, resource_id: str, outcome: str,
    details: dict[str, Any] | None = None,
) -> None:
    database_url = get_database_url()
    if not database_url:
        return
    import psycopg
    from psycopg.types.json import Json
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """INSERT INTO audit_logs (
                       audit_id, actor_id, actor_role, action, resource_type,
                       resource_id, outcome, details
                   ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (f"AUD-{uuid4().hex}", actor_id, actor_role, action,
                 resource_type, resource_id, outcome, Json(details or {})),
            )
