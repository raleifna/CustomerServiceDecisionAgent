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


def _create_service_request_in_memory(
    *,
    order_id: str,
    customer_id: str,
    request_type: str,
    request_reason: str,
    requested_quantity: int,
    order_item: dict[str, Any] | None = None,
    order_item_count: int = 1,
    request_date: date | None = None,
) -> tuple[dict[str, Any], bool]:
    """Stand-in for create_service_request that needs no real database.

    create_service_request() requires DATABASE_URL, which the evaluation
    harness deliberately does not configure (eval must stay free and
    independent of external services). This fabricates the same shape of
    record so the intake node's downstream behavior is still exercised.
    """
    return (
        {
            "request_id": "SR-EVAL-INTAKE",
            "order_id": order_id,
            "customer_id": customer_id,
            "request_type": request_type,
            "request_reason": request_reason,
            "requested_quantity": requested_quantity,
            "request_date": (request_date or date(2026, 8, 17)).isoformat(),
            "workflow_status": "Pending",
            "decision": None,
            "approved_quantity": None,
            "approved_amount": None,
            "decision_confidence": None,
        },
        True,
    )


def _run_intake_case(case: dict[str, Any]) -> dict[str, Any]:
    """Run a conversational service-request creation flow.

    Overrides request_intent_agent.chat_json (blocked to a hard error for the
    rest of the suite, see run_all()) with the case's mock_extraction, so this
    verifies that a correctly-classified request_reason (e.g. "尺码不对" ->
    "不想要了") is wired through to the created service request. It does not
    verify that a real LLM produces that classification — see docs/07_Evaluation.md
    for why the harness cannot depend on live LLM behavior.
    """
    config = {
        "configurable": {
            "thread_id": f"eval-{case['case_id']}-{uuid4().hex}",
        }
    }
    output: dict[str, Any] = {}

    with (
        patch(
            "src.backend.agents.request_intent_agent.chat_json",
            return_value=case["mock_extraction"],
        ),
        patch(
            "src.backend.actions.service_request_action.create_service_request",
            side_effect=_create_service_request_in_memory,
        ),
    ):
        for index, turn in enumerate(case["turns"]):
            invoke_input: dict[str, Any] = {"user_message": turn["user_message"]}
            if index == 0:
                invoke_input["conversation_context"] = {
                    "authenticated_customer_id": case["customer_id"],
                }
            output = customer_service_conversation_graph.invoke(
                invoke_input,
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

                elif mode == "conversation_intake":
                    output = _run_intake_case(case)
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


def _mock_query_tool_call(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    tool_executor: Any,
    **_kwargs: Any,
) -> str:
    """Deterministic stand-in for chat_with_tools in query_agent.py.

    Calls every available tool (instead of letting a real model choose) and
    renders a plain, deterministic sentence from the results. This verifies
    the tool-execution plumbing without depending on real model output; it
    does not verify that a real LLM picks the right tools or phrases well.
    """
    parts: list[str] = []
    for tool in tools:
        name = tool["function"]["name"]
        result = tool_executor(name, {})
        if name == "get_order_status":
            order_id = result.get("order_id")
            parts.append(f"订单 {order_id} 支付状态 {result.get('payment_status')}。")
            promised_ship_date = (result.get("expedite") or {}).get("promised_ship_date")
            if result.get("delivered_date"):
                parts.append(f"已于 {result['delivered_date']} 签收。")
            elif result.get("shipped_date"):
                parts.append(f"已于 {result['shipped_date']} 发货")
                if result.get("estimated_delivery_date"):
                    parts.append(f"，预计 {result['estimated_delivery_date']} 送达。")
                else:
                    parts.append("。")
            elif promised_ship_date:
                parts.append(f"尚未发货，承诺发货日期为 {promised_ship_date}。")
            else:
                parts.append("尚未发货。")
        elif name == "get_logistics_status":
            exceptions = result.get("exceptions") or []
            if not exceptions:
                parts.append("物流没有异常记录。")
            else:
                for exc in exceptions:
                    status = "已处理" if exc.get("resolved") else "尚未处理"
                    parts.append(
                        f"{exc.get('carrier')} 记录 {exc.get('exception_type')}（{status}）。"
                    )
    return "".join(parts) or "暂时没有可查询到的信息。"


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
        patch(
            "src.backend.agents.query_agent.chat_with_tools",
            side_effect=_mock_query_tool_call,
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
