import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from src.backend.config.business_rules import load_business_rules


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_RULES_PATH = PROJECT_ROOT / "data" / "decision_rules.json"
SUPPORTED_OPERATORS = {"eq", "gt", "gte", "lt", "lte", "in", "contains"}


class DecisionRulesError(ValueError):
    pass


def _get(data: dict[str, Any], path: str, default: Any = None) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


@lru_cache(maxsize=4)
def load_decision_rules(path: str | None = None) -> list[dict[str, Any]]:
    configured = path or os.getenv("DECISION_RULES_PATH")
    file_path = Path(configured) if configured else DEFAULT_RULES_PATH
    try:
        document = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DecisionRulesError(f"无法加载裁决规则 {file_path}: {exc}") from exc
    rules = document.get("rules") if isinstance(document, dict) else None
    if not isinstance(rules, list) or not rules:
        raise DecisionRulesError("裁决规则必须是非空 rules 数组")
    ids: set[str] = set()
    for rule in rules:
        if not isinstance(rule, dict) or not isinstance(rule.get("id"), str):
            raise DecisionRulesError("每条裁决规则必须包含字符串 id")
        if rule["id"] in ids:
            raise DecisionRulesError(f"裁决规则编号重复: {rule['id']}")
        ids.add(rule["id"])
        if not isinstance(rule.get("priority"), (int, float)):
            raise DecisionRulesError(f"{rule['id']}.priority 必须是数字")
        conditions = rule.get("when")
        if not isinstance(conditions, list) or not conditions:
            raise DecisionRulesError(f"{rule['id']}.when 必须是非空数组")
        for condition in conditions:
            if condition.get("op") not in SUPPORTED_OPERATORS or not condition.get("path"):
                raise DecisionRulesError(f"{rule['id']} 包含不支持的条件")
    return sorted(rules, key=lambda item: item["priority"], reverse=True)


def _matches(condition: dict[str, Any], context: dict[str, Any]) -> bool:
    actual = _get(context, condition["path"])
    expected = condition.get("value")
    op = condition["op"]
    try:
        if op == "eq":
            return actual == expected
        if op == "gt":
            return actual > expected
        if op == "gte":
            return actual >= expected
        if op == "lt":
            return actual < expected
        if op == "lte":
            return actual <= expected
        if op == "in":
            return actual in expected
        if op == "contains":
            return expected in actual
    except (TypeError, ValueError):
        return False
    return False


def _resolve_value(spec: Any, context: dict[str, Any]) -> Any:
    if not isinstance(spec, dict):
        return spec
    op = spec.get("op")
    if op == "path":
        value = _get(context, spec["path"])
        if value is None and "default_from" in spec:
            value = _get(context, spec["default_from"])
        return spec.get("default") if value is None else value
    if op == "min":
        values = [_get(context, path) for path in spec.get("paths", [])]
        return min(values) if values and all(isinstance(v, int) for v in values) else None
    if op == "unit_price_times_quantity":
        allowed_types = spec.get("request_types")
        if allowed_types and _get(context, "service_request.request_type") not in allowed_types:
            return None
        price = _get(context, "order_item.unit_price")
        if not isinstance(price, (int, float)):
            price = _get(context, "product.price")
        quantity = context.get("approved_quantity")
        if not isinstance(price, (int, float)) or not isinstance(quantity, int):
            return None
        return round(float(price) * quantity, 2)
    if op == "partial_if_less":
        requested = _get(context, spec["requested"])
        approved = _get(context, spec["approved"])
        return "PartialApprove" if approved < requested else "Approve"
    raise DecisionRulesError(f"不支持的输出表达式: {op}")


def _format_reasons(spec: Any, context: dict[str, Any]) -> list[str]:
    reasons = _resolve_value(spec, context)
    if not isinstance(reasons, list):
        return []
    formatted = []
    for reason in reasons:
        text = str(reason)
        for path in ("order_evidence.confirmed_missing_quantity", "approved_quantity"):
            text = text.replace("{" + path + "}", str(_get(context, path, "")))
        formatted.append(text)
    return formatted


def evaluate_decision(context: dict[str, Any]) -> dict[str, Any] | None:
    """Evaluate the highest-priority matching declarative decision rule."""
    runtime = {**context, "business_rules": load_business_rules()}
    for rule in load_decision_rules():
        if not all(_matches(condition, runtime) for condition in rule["when"]):
            continue
        approved_quantity = _resolve_value(rule.get("approved_quantity"), runtime)
        runtime["approved_quantity"] = approved_quantity
        rule_hits = list(rule.get("rule_hits", []))
        for policy_id in rule.get("optional_rule_hits", []):
            if policy_id in runtime.get("policy_ids", set()):
                rule_hits.append(policy_id)
        return {
            "matched_rule_id": rule["id"],
            "decision": _resolve_value(rule["decision"], runtime),
            "decision_confidence": float(_resolve_value(rule["confidence"], runtime)),
            "approved_quantity": approved_quantity,
            "approved_amount": _resolve_value(rule.get("approved_amount"), runtime),
            "rule_hits": rule_hits,
            "reason_chain": _format_reasons(rule.get("reasons", []), runtime),
        }
    return None


def automatic_policy_ids() -> set[str]:
    """Policies that can lead to an automatic decision in the active registry."""
    return {
        condition["value"]
        for rule in load_decision_rules()
        for condition in rule["when"]
        if condition.get("path") == "policy_ids"
        and condition.get("op") == "contains"
        and isinstance(condition.get("value"), str)
    }


def decision_rules_version(path: str | None = None) -> str:
    configured = path or os.getenv("DECISION_RULES_PATH")
    file_path = Path(configured) if configured else DEFAULT_RULES_PATH
    try:
        document = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DecisionRulesError(f"无法读取裁决规则版本 {file_path}: {exc}") from exc
    return str(document.get("version", "unknown"))
