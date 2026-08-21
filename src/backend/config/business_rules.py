import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_RULES_PATH = PROJECT_ROOT / "data" / "business_rules.json"


class BusinessRulesError(ValueError):
    """Raised when the business-rules file is missing or malformed."""


def _require_number(data: dict[str, Any], section: str, key: str) -> float:
    value = data.get(section, {}).get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise BusinessRulesError(f"{section}.{key} 必须是非负数字")
    return float(value)


@lru_cache(maxsize=4)
def load_business_rules(path: str | None = None) -> dict[str, Any]:
    """Load validated rules; BUSINESS_RULES_PATH supports per-environment policy."""
    configured_path = path or os.getenv("BUSINESS_RULES_PATH")
    file_path = Path(configured_path) if configured_path else DEFAULT_RULES_PATH
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BusinessRulesError(f"无法加载业务规则 {file_path}: {exc}") from exc

    if not isinstance(data, dict):
        raise BusinessRulesError("业务规则顶层必须是 JSON 对象")
    for section, key in (
        ("refund", "no_reason_return_days"),
        ("refund", "quality_claim_days"),
        ("refund", "late_non_quality_days"),
        ("risk", "high_refund_count"),
        ("risk", "high_value_amount"),
        ("risk", "new_account_days"),
        ("risk", "max_after_sales_submissions_per_order_30_days"),
        ("decision", "default_evidence_confidence"),
    ):
        _require_number(data, section, key)
    return data
