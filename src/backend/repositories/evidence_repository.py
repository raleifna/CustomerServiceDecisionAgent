from datetime import date
from typing import Any

from src.backend.repositories.postgres_repository import get_database_url


EVIDENCE_FIELDS = """
    evidence_id, request_id, evidence_type, submitted_by, submitted_date,
    original_filename, media_type, storage_path, file_size_bytes,
    verification_status, verified_facts, verification_confidence
"""


def _normalize(record: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(record)
    if isinstance(normalized.get("submitted_date"), date):
        normalized["submitted_date"] = normalized["submitted_date"].isoformat()
    return normalized


def create_evidence_record(
    *,
    request_id: str,
    original_filename: str,
    media_type: str,
    storage_path: str,
    file_size_bytes: int,
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
            file_size_bytes, verification_status
        )
        VALUES (
            'EVD' || to_char(CURRENT_DATE, 'YYYYMM') ||
            lpad(nextval('evidence_sequence')::text, 4, '0'),
            %s, 'Image', 'Customer', CURRENT_DATE, %s, %s, %s, %s, 'Pending'
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
