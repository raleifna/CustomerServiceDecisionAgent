import json
import tempfile
from datetime import date
from pathlib import Path
from threading import Lock
from typing import Any

from src.backend.repositories.mock_db_repository import MOCK_DB_DIR
from src.backend.repositories.postgres_repository import (
    create_postgres_expedite_request,
    get_database_url,
)


FULFILLMENT_REQUESTS_FILE = MOCK_DB_DIR / "fulfillment_requests.json"
OPEN_WORKFLOW_STATUSES = {"Submitted", "InProgress"}
_write_lock = Lock()


def load_fulfillment_requests(
    file_path: Path = FULFILLMENT_REQUESTS_FILE,
) -> list[dict[str, Any]]:
    if not file_path.exists():
        return []

    with file_path.open("r", encoding="utf-8") as file:
        records = json.load(file)

    if not isinstance(records, list):
        raise ValueError("fulfillment_requests.json 的顶层结构必须是 JSON 数组")

    return records


def find_open_expedite_request(
    order_id: str,
    records: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    current_records = records if records is not None else load_fulfillment_requests()

    for record in current_records:
        if (
            record.get("order_id") == order_id
            and record.get("request_type") == "ExpediteShipment"
            and record.get("workflow_status") in OPEN_WORKFLOW_STATUSES
        ):
            return record

    return None


def _next_request_id(
    records: list[dict[str, Any]],
    created_date: date,
) -> str:
    prefix = f"FR{created_date:%Y%m}"
    sequences = []

    for record in records:
        request_id = record.get("fulfillment_request_id")
        if (
            isinstance(request_id, str)
            and request_id.startswith(prefix)
            and request_id[len(prefix):].isdigit()
        ):
            sequences.append(int(request_id[len(prefix):]))

    return f"{prefix}{max(sequences, default=0) + 1:04d}"


def _write_records_atomically(
    records: list[dict[str, Any]],
    file_path: Path,
) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=file_path.parent,
            prefix=f".{file_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            json.dump(records, temp_file, ensure_ascii=False, indent=2)
            temp_file.write("\n")
            temp_path = Path(temp_file.name)

        temp_path.replace(file_path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


def create_expedite_request(
    order_id: str,
    customer_id: str,
    *,
    created_date: date | None = None,
    file_path: Path | None = None,
) -> tuple[dict[str, Any], bool]:
    """Create one open expedite request, or return the existing one."""
    request_date = created_date or date.today()

    # An explicit path is used by isolated tests. Runtime uses PostgreSQL when
    # DATABASE_URL is configured, and JSON remains a local-development fallback.
    if file_path is None:
        database_url = get_database_url()
        if database_url:
            return create_postgres_expedite_request(
                database_url,
                order_id,
                customer_id,
                request_date,
            )
        file_path = FULFILLMENT_REQUESTS_FILE

    with _write_lock:
        records = load_fulfillment_requests(file_path)
        existing = find_open_expedite_request(order_id, records)
        if existing is not None:
            return existing, False

        record = {
            "fulfillment_request_id": _next_request_id(records, request_date),
            "order_id": order_id,
            "customer_id": customer_id,
            "request_type": "ExpediteShipment",
            "created_date": request_date.isoformat(),
            "workflow_status": "Submitted",
            "result": None,
        }
        records.append(record)
        _write_records_atomically(records, file_path)

    return record, True
