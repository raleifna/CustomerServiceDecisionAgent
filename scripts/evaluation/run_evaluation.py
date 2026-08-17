import argparse
import json
import os
import sys
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Evaluation must be deterministic, free, and independent of API availability.
os.environ["ENABLE_LLM_RESPONSE"] = "false"

from src.backend.actions.fulfillment_action import fulfillment_action_node
from src.backend.graph.workflow import (
    customer_service_conversation_graph,
    customer_service_graph,
)
from src.backend.repositories.fulfillment_repository import (
    create_expedite_request,
)


EVALUATION_DIR = PROJECT_ROOT / "data" / "evaluation"
MISSING = object()


@dataclass
class EvaluationResult:
    case_id: str
    category: str
    passed: bool
    errors: list[str]


def _load_cases(filename: str) -> list[dict[str, Any]]:
    file_path = EVALUATION_DIR / filename
    with file_path.open("r", encoding="utf-8") as file:
        cases = json.load(file)

    if not isinstance(cases, list):
        raise ValueError(f"{filename} 顶层结构必须是 JSON 数组")
    return cases


def _get_path(data: dict[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return MISSING
        current = current[part]
    return current


def _validate_output(
    output: dict[str, Any],
    expected_paths: dict[str, Any] | None = None,
    response_contains: list[str] | None = None,
    response_not_contains: list[str] | None = None,
) -> list[str]:
    errors = []

    for path, expected in (expected_paths or {}).items():
        actual = _get_path(output, path)
        if actual is MISSING:
            errors.append(f"缺少字段 {path}")
        elif actual != expected:
            errors.append(f"{path}: expected={expected!r}, actual={actual!r}")

    response = output.get("customer_response") or ""
    for text in response_contains or []:
        if text not in response:
            errors.append(f"回复缺少文本 {text!r}: {response!r}")
    for text in response_not_contains or []:
        if text in response:
            errors.append(f"回复包含禁止文本 {text!r}: {response!r}")

    return errors


def _result_for_case(
    case: dict[str, Any],
    errors: list[str],
) -> EvaluationResult:
    return EvaluationResult(
        case_id=case["case_id"],
        category=case["category"],
        passed=not errors,
        errors=errors,
    )


def run_single_turn_cases() -> list[EvaluationResult]:
    results = []
    for case in _load_cases("single_turn_cases.json"):
        try:
            output = customer_service_graph.invoke(case["input"])
            errors = _validate_output(
                output,
                case.get("expected_paths"),
                case.get("response_contains"),
                case.get("response_not_contains"),
            )
        except Exception as exc:
            errors = [f"运行异常: {type(exc).__name__}: {exc}"]
        results.append(_result_for_case(case, errors))
    return results


def run_multi_turn_cases() -> list[EvaluationResult]:
    results = []
    for case in _load_cases("multi_turn_cases.json"):
        errors = []
        config = {
            "configurable": {
                "thread_id": f"eval-{case['case_id']}-{uuid4().hex}",
            }
        }

        try:
            for index, turn in enumerate(case["turns"], start=1):
                output = customer_service_conversation_graph.invoke(
                    {"user_message": turn["user_message"]},
                    config=config,
                )
                turn_errors = _validate_output(
                    output,
                    turn.get("expected_paths"),
                    turn.get("response_contains"),
                    turn.get("response_not_contains"),
                )
                errors.extend(f"turn {index}: {error}" for error in turn_errors)
        except Exception as exc:
            errors.append(f"运行异常: {type(exc).__name__}: {exc}")

        results.append(_result_for_case(case, errors))
    return results


def _run_conversation_action(
    case: dict[str, Any],
    temp_file: Path,
) -> dict[str, Any]:
    def create_in_temp_db(order_id: str, customer_id: str):
        return create_expedite_request(
            order_id,
            customer_id,
            created_date=date(2026, 8, 17),
            file_path=temp_file,
        )

    config = {
        "configurable": {
            "thread_id": f"eval-{case['case_id']}-{uuid4().hex}",
        }
    }
    output: dict[str, Any] = {}

    with patch(
        "src.backend.actions.fulfillment_action.create_expedite_request",
        side_effect=create_in_temp_db,
    ):
        for turn in case["turns"]:
            output = customer_service_conversation_graph.invoke(
                {"user_message": turn["user_message"]},
                config=config,
            )

    return output


def _run_failed_action(case: dict[str, Any]) -> dict[str, Any]:
    config = {
        "configurable": {
            "thread_id": f"eval-{case['case_id']}-{uuid4().hex}",
        }
    }
    output: dict[str, Any] = {}

    with patch(
        "src.backend.actions.fulfillment_action.create_expedite_request",
        side_effect=OSError("simulated write failure"),
    ):
        for turn in case["turns"]:
            output = customer_service_conversation_graph.invoke(
                {"user_message": turn["user_message"]},
                config=config,
            )

    return output


def run_action_cases() -> list[EvaluationResult]:
    results = []

    for case in _load_cases("action_cases.json"):
        errors = []
        try:
            mode = case["mode"]
            with TemporaryDirectory() as directory:
                temp_file = Path(directory) / "fulfillment_requests.json"
                temp_file.write_text("[]\n", encoding="utf-8")

                if mode == "conversation_submit":
                    output = _run_conversation_action(case, temp_file)
                    errors.extend(
                        _validate_output(
                            output,
                            case.get("expected_paths"),
                            case.get("response_contains"),
                            case.get("response_not_contains"),
                        )
                    )
                    record_count = len(
                        json.loads(temp_file.read_text(encoding="utf-8"))
                    )
                    if record_count != 1:
                        errors.append(
                            f"expected 1 fulfillment record, actual={record_count}"
                        )

                elif mode == "repository_idempotency":
                    first, first_created = create_expedite_request(
                        case["order_id"],
                        case["customer_id"],
                        created_date=date(2026, 8, 17),
                        file_path=temp_file,
                    )
                    second, second_created = create_expedite_request(
                        case["order_id"],
                        case["customer_id"],
                        created_date=date(2026, 8, 17),
                        file_path=temp_file,
                    )
                    records = json.loads(temp_file.read_text(encoding="utf-8"))
                    if not first_created or second_created:
                        errors.append("幂等创建标记不正确")
                    if first["fulfillment_request_id"] != second["fulfillment_request_id"]:
                        errors.append("重复调用返回了不同催单编号")
                    if len(records) != case["expected_record_count"]:
                        errors.append(
                            f"record count expected={case['expected_record_count']}, "
                            f"actual={len(records)}"
                        )

                elif mode == "conversation_failure":
                    output = _run_failed_action(case)
                    errors.extend(
                        _validate_output(
                            output,
                            case.get("expected_paths"),
                            case.get("response_contains"),
                            case.get("response_not_contains"),
                        )
                    )

                elif mode == "already_shipped":
                    output = fulfillment_action_node(
                        {
                            "conversation_result": {
                                "order_id": case["order_id"],
                            },
                            "conversation_context": {},
                        }
                    )
                    errors.extend(
                        _validate_output(
                            output,
                            case.get("expected_paths"),
                            case.get("response_contains"),
                            case.get("response_not_contains"),
                        )
                    )
                else:
                    errors.append(f"未知 action mode: {mode}")
        except Exception as exc:
            errors.append(f"运行异常: {type(exc).__name__}: {exc}")

        results.append(_result_for_case(case, errors))

    return results


def run_all() -> list[EvaluationResult]:
    llm_error = RuntimeError("Evaluation Harness 禁止调用真实 LLM")
    with (
        patch(
            "src.backend.agents.conversation_router_agent.chat_json",
            side_effect=llm_error,
        ),
        patch(
            "src.backend.agents.request_intent_agent.chat_json",
            side_effect=llm_error,
        ),
    ):
        return (
            run_single_turn_cases()
            + run_multi_turn_cases()
            + run_action_cases()
        )


def _print_summary(results: list[EvaluationResult]) -> None:
    print("\nCustomer Service Agent Evaluation")
    print("=" * 36)

    category_totals = Counter(result.category for result in results)
    category_passed = Counter(
        result.category for result in results if result.passed
    )

    for category in sorted(category_totals):
        passed = category_passed[category]
        total = category_totals[category]
        print(f"{category:20} {passed:>2}/{total:<2}  {passed / total:>7.1%}")

    total_passed = sum(result.passed for result in results)
    print("-" * 36)
    print(
        f"overall              {total_passed:>2}/{len(results):<2}  "
        f"{total_passed / len(results):>7.1%}"
    )

    failures = [result for result in results if not result.passed]
    if failures:
        print("\nFailures")
        for result in failures:
            print(f"- {result.case_id} ({result.category})")
            for error in result.errors:
                print(f"  {error}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run deterministic agent evaluation")
    parser.add_argument(
        "--report",
        type=Path,
        help="Optional path for a JSON evaluation report",
    )
    args = parser.parse_args()

    results = run_all()
    _print_summary(results)

    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(
                {
                    "passed": sum(result.passed for result in results),
                    "total": len(results),
                    "results": [asdict(result) for result in results],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    return 0 if all(result.passed for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
