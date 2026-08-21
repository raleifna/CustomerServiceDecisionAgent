from datetime import date
from typing import Any

from src.backend.repositories.mock_db_repository import get_evi as get_seed_evidence
from src.backend.repositories.postgres_repository import get_database_url


EVIDENCE_FIELDS = """
    evidence_id, request_id, evidence_type, submitted_by, submitted_date,
    original_filename, media_type, storage_path, file_size_bytes,
    verification_status, verified_facts, verification_confidence,
    reviewed_by, review_notes, reviewed_at, customer_description
"""


def _normalize(record: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(record)
    if isinstance(normalized.get("submitted_date"), date):
        normalized["submitted_date"] = normalized["submitted_date"].isoformat()
    reviewed_at = normalized.get("reviewed_at")
    if reviewed_at is not None and not isinstance(reviewed_at, str):
        normalized["reviewed_at"] = reviewed_at.isoformat()
    return normalized


def create_evidence_record(
    *,
    request_id: str,
    original_filename: str,
    media_type: str,
    storage_path: str,
    file_size_bytes: int,
    customer_description: str,
) -> dict[str, Any]:
    database_url = get_database_url()
    if not database_url:
        raise RuntimeError("提交证据需要配置 DATABASE_URL")

    import psycopg
    from psycopg.rows import dict_row

    sql = f"""
        INSERT INTO service_request_evidence (
            evidence_id, request_id, evidence_type, submitted_by,
            submitted_date, original_filename, media_type, storage_path,
            file_size_bytes, verification_status, customer_description
        )
        VALUES (
            'EVD' || to_char(CURRENT_DATE, 'YYYYMM') ||
            lpad(nextval('evidence_sequence')::text, 4, '0'),
            %s, 'Image', 'Customer', CURRENT_DATE, %s, %s, %s, %s, 'Pending', %s
        )
        RETURNING {EVIDENCE_FIELDS}
    """
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                sql,
                (
                    request_id,
                    original_filename,
                    media_type,
                    storage_path,
                    file_size_bytes,
                    customer_description,
                ),
            )
            record = cursor.fetchone()
    if record is None:
        raise RuntimeError("证据元数据创建失败")
    return _normalize(dict(record))


def list_evidence_records(request_id: str) -> list[dict[str, Any]]:
    database_url = get_database_url()
    if not database_url:
        return []

    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT {EVIDENCE_FIELDS} FROM service_request_evidence "
                "WHERE request_id = %s ORDER BY created_at",
                (request_id,),
            )
            return [_normalize(dict(record)) for record in cursor.fetchall()]


def get_evidence_for_decision(request_id: str) -> list[dict[str, Any]]:
    """Merge seeded demo evidence with real customer uploads.

    evidence_agent_node (the decision graph's evidence collector) must see
    both: the pre-built JSON demo cases used by the seed data, and evidence
    customers actually upload through the API, which only lives in Postgres.
    Reading just one or the other silently drops evidence from decisions.
    """
    records = list(get_seed_evidence(request_id))
    seed_ids = {record.get("evidence_id") for record in records}
    records.extend(
        record
        for record in list_evidence_records(request_id)
        if record.get("evidence_id") not in seed_ids
    )
    return records


def list_pending_evidence_for_staff_queue() -> list[dict[str, Any]]:
    """List evidence still waiting on a human verification decision."""
    database_url = get_database_url()
    if not database_url:
        return []

    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT {EVIDENCE_FIELDS}
                FROM service_request_evidence
                WHERE verification_status = 'Pending'
                ORDER BY created_at
                """
            )
            return [_normalize(dict(record)) for record in cursor.fetchall()]


def review_evidence(
    evidence_id: str,
    *,
    verification_status: str,
    reviewed_by: str,
    review_notes: str | None,
) -> dict[str, Any] | None:
    """Record a human agent's verification decision on submitted evidence."""
    database_url = get_database_url()
    if not database_url:
        raise RuntimeError("审核证据需要配置 DATABASE_URL")

    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                UPDATE service_request_evidence
                SET verification_status = %s,
                    reviewed_by = %s,
                    review_notes = %s,
                    reviewed_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE evidence_id = %s
                RETURNING {EVIDENCE_FIELDS}
                """,
                (verification_status, reviewed_by, review_notes, evidence_id),
            )
            record = cursor.fetchone()

    return _normalize(dict(record)) if record else None
