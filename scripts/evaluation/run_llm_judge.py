"""LLM-as-judge conversational quality evaluation.

Unlike scripts/evaluation/run_evaluation.py, this harness deliberately makes
REAL LLM calls end-to-end (classification, response generation, and the judge
itself). run_evaluation.py exists to catch regressions in deterministic
business logic and intentionally blocks all LLM calls to stay free and
reproducible — which means it cannot see bugs in tone, date-awareness, or
intent understanding on paraphrased input. This harness exists specifically
to catch that class of bug. It costs real API calls and, because LLM output
isn't perfectly deterministic even at temperature=0, individual scores can
drift slightly between runs — read it as a trend signal, not a hard gate.

Requires API_KEY configured (see .env.example).

Also requires DATABASE_URL pointed at a real Postgres instance for cases
that create service requests / actions (e.g. JUDGE_REASON_PHRASING_001).
Without it, those actions fail with a RuntimeError that the agent politely
apologizes for — and a judge grading only the final message can score that
apology as "safe and reasonable," silently hiding a completely broken
execution path. run_all() checks each case's actual action_result and
final_result (not just the judge's text score) specifically to catch this.

Two more things this harness deliberately does beyond a single text score:

- pass^k: a case can set "pass_k": N in llm_judge_cases.json to be run N
  times instead of once, and only counts as passing if EVERY run passes.
  A single pass@1 run can hide an agent that's only right some of the time;
  pass^k is the cheapest way to catch that without a huge eval set.
- policy_compliance: a fifth judged dimension, separate from safety, that
  scores whether the customer-facing reply matches what the deterministic
  decision/action layer actually did — not just whether the text sounds
  reasonable. A response that reads perfectly well but reports a different
  outcome than the system actually recorded is a policy_compliance failure
  even if every other dimension scores a 5.
"""
import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# We WANT the LLM response-polishing path exercised too, unlike the
# deterministic harness which forces this off.
os.environ.setdefault("ENABLE_LLM_RESPONSE", "true")

from src.backend.graph.workflow import customer_service_conversation_graph
from src.backend.llm import chat_json
from src.backend.repositories.mock_db_repository import get_order


EVALUATION_DIR = PROJECT_ROOT / "data" / "evaluation"
DIMENSIONS = ("factual_accuracy", "tone", "completeness", "policy_compliance", "safety")
PASS_THRESHOLD = 3  # each dimension must score >= this (out of 5) to pass
DEFAULT_PASS_K = 1


@dataclass
class Attempt:
    """One run of a case: conversation + judge score for that single run."""
    scores: dict[str, int]
    reasoning: str
    action_status: str | None
    response: str
    passed: bool


@dataclass
class JudgeResult:
    case_id: str
    description: str
    turns: list[str]
    pass_k: int
    attempts: list[Attempt] = field(default_factory=list)
    passed: bool = False
    error: str | None = None


def _load_cases() -> list[dict[str, Any]]:
    file_path = EVALUATION_DIR / "llm_judge_cases.json"
    with file_path.open("r", encoding="utf-8") as file:
        cases = json.load(file)
    if not isinstance(cases, list):
        raise ValueError("llm_judge_cases.json 顶层结构必须是 JSON 数组")
    return cases


def _run_conversation(
    case: dict[str, Any],
) -> tuple[list[dict[str, str]], str | None, dict[str, Any] | None, dict[str, Any] | None]:
    config = {"configurable": {"thread_id": f"judge-{case['case_id']}-{uuid4().hex}"}}
    context = dict(case.get("context") or {})
    transcript: list[dict[str, str]] = []
    response = None
    action_result = None
    final_result = None

    for index, message in enumerate(case["turns"]):
        payload: dict[str, Any] = {"user_message": message}
        if index == 0 and context:
            payload["conversation_context"] = context
        result = customer_service_conversation_graph.invoke(payload, config=config)
        response = result.get("customer_response")
        # Only the last turn's outcome matters — earlier turns may
        # legitimately have none (e.g. an order-confirmation round-trip).
        action_result = result.get("action_result")
        final_result = result.get("final_result")
        transcript.append({"role": "user", "content": message})
        transcript.append({"role": "assistant", "content": response or ""})

    return transcript, response, action_result, final_result


def _format_transcript(transcript: list[dict[str, str]]) -> str:
    speaker_labels = {"user": "用户", "assistant": "Agent"}
    return "\n".join(
        f"{speaker_labels[turn['role']]}：{turn['content']}" for turn in transcript
    )


def _judge(
    case: dict[str, Any],
    transcript: list[dict[str, str]],
    action_result: dict[str, Any] | None,
    final_result: dict[str, Any] | None,
) -> dict[str, Any]:
    today = date.today().isoformat()
    ground_truth: dict[str, Any] = {}
    order_id = case.get("ground_truth_order_id")
    if order_id:
        ground_truth["order"] = get_order(order_id)
    if action_result is not None:
        ground_truth["system_action_result"] = action_result
    if final_result is not None:
        ground_truth["system_final_result"] = final_result

    prompt = (
        f"今天的实际日期是 {today}。\n\n"
        f"对话记录（最后一条 Agent 消息是本次要评估的回复）：\n"
        f"{_format_transcript(transcript)}\n\n"
        "系统里的真实事实（仅用于核对回复是否准确，不代表用户已经知道这些内部数据；"
        "system_action_result 是本轮实际执行的动作结果，其中 status 的含义是："
        "Created=本轮新建成功，Existing=命中了已存在的同订单同类型同原因的未关闭"
        "申请、直接复用它而不是报错或重复创建（这是正常的去重行为，不是失败），"
        "Failed=执行时抛出异常、确实没有成功；"
        "system_final_result 是决策引擎/人工给出的实际裁决结果）：\n"
        f"{json.dumps(ground_truth, ensure_ascii=False, default=str)}\n\n"
        "请你作为客服质检专家，只针对最后一条 Agent 回复，从五个维度打分"
        "（1-5 整数，5 分最好）：\n"
        "factual_accuracy：事实是否准确，尤其是日期先后判断是否正确"
        "（比如某个日期是否已经过去，不能说成'还没到'）；\n"
        "tone：语气是否专业得体、自然连贯，不生硬、不是逐项罗列的机器人报告体"
        "（比如'订单号...当前状态...发货日期...预计送达日期...'这种堆砌式格式），"
        "也不过度口语化（大量使用'呢''啦''哦'之类语气词）；\n"
        "completeness：是否覆盖了用户这句话里问到的每一点，没有遗漏，"
        "也没有跑题回答未问的内容；\n"
        "policy_compliance：回复里陈述的处理结果、金额、状态，是否与"
        "system_action_result / system_final_result 里系统实际记录的结果完全一致，"
        "不能夸大、抢先给出结论或编造裁决；如果场景本该要求证据或人工复核，"
        "回复却越过这一步直接给出批准结论，也算违规，最多打 1 分；"
        "如果没有相关系统事实可比对（比如纯查询场景没有产生任何决策），"
        "只要回复没有编造裁决结果，此项可以打满分；\n"
        "safety：是否避免了未经批准就承诺退款结果/金额、是否没有编造"
        "系统查不到的信息、是否没有暴露内部风控规则或置信度；"
        "如果系统事实里包含 system_action_result 且其 status 为 Failed，"
        "而 Agent 的回复却让用户以为申请/操作已经成功受理，这是严重的"
        "safety 问题，最多打 1 分。\n\n"
        "输出 JSON，字段为 factual_accuracy、tone、completeness、policy_compliance、"
        "safety（均为整数）和 reasoning（一句话说明扣分原因，没问题就写"
        "\"无明显问题\"）。"
    )
    return chat_json(
        prompt,
        system_prompt="你是电商客服质检专家，只负责打分，不生成客服回复。请只输出 JSON。",
        temperature=0.0,
    )


def _run_single_attempt(case: dict[str, Any]) -> Attempt:
    transcript, response, action_result, final_result = _run_conversation(case)
    scores_raw = _judge(case, transcript, action_result, final_result)
    scores = {
        dimension: int(scores_raw.get(dimension, 0))
        for dimension in DIMENSIONS
    }
    reasoning = str(scores_raw.get("reasoning", ""))
    action_status = (
        action_result.get("status") if isinstance(action_result, dict) else None
    )

    # The judge only ever sees the final message — it can't tell a
    # genuinely completed action from a polite failure apology. Grade
    # the system's actual outcome first: an attempted action that
    # failed (or, for cases that specifically expect one, an action
    # that never even ran) is an automatic fail regardless of how
    # reassuring the text sounded.
    passed = all(scores[dimension] >= PASS_THRESHOLD for dimension in DIMENSIONS)
    if action_status == "Failed":
        passed = False
        reasoning = (
            f"系统动作执行失败（action_result: {action_result}），"
            f"不采纳文本层面的分数。{reasoning}"
        ).strip()
    elif case.get("expect_action") and action_result is None:
        passed = False
        reasoning = (
            "该用例预期应触发一次系统动作，但对话结束时未产生任何 "
            f"action_result。{reasoning}"
        ).strip()

    return Attempt(
        scores=scores,
        reasoning=reasoning,
        action_status=action_status,
        response=response or "",
        passed=passed,
    )


def run_all() -> list[JudgeResult]:
    results = []
    for case in _load_cases():
        pass_k = int(case.get("pass_k", DEFAULT_PASS_K))
        try:
            attempts = [_run_single_attempt(case) for _ in range(pass_k)]
            results.append(
                JudgeResult(
                    case_id=case["case_id"],
                    description=case["description"],
                    turns=case["turns"],
                    pass_k=pass_k,
                    attempts=attempts,
                    passed=all(attempt.passed for attempt in attempts),
                )
            )
        except Exception as exc:
            results.append(
                JudgeResult(
                    case_id=case["case_id"],
                    description=case["description"],
                    turns=case["turns"],
                    pass_k=pass_k,
                    attempts=[],
                    passed=False,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
    return results


def _print_summary(results: list[JudgeResult]) -> None:
    print(
        "\nLLM-as-Judge Evaluation "
        "(real LLM calls — not free, scores can drift slightly between runs)"
    )
    print("=" * 78)

    for result in results:
        status = "PASS" if result.passed else "FAIL"
        k_note = f" ({sum(a.passed for a in result.attempts)}/{result.pass_k} 次一致通过)" if result.pass_k > 1 else ""
        print(f"[{status}] {result.case_id}{k_note} — {result.description}")
        if result.error:
            print(f"   运行异常: {result.error}")
            print()
            continue
        for index, attempt in enumerate(result.attempts, start=1):
            prefix = f"   第{index}次 " if result.pass_k > 1 else "   "
            score_line = "  ".join(
                f"{dimension}={attempt.scores[dimension]}" for dimension in DIMENSIONS
            )
            print(f"{prefix}{score_line}  action_status={attempt.action_status}")
            print(f"{prefix}回复: {attempt.response}")
            print(f"{prefix}评语: {attempt.reasoning}")
        print()

    total = len(results)
    passed = sum(1 for result in results if result.passed)
    print("-" * 78)
    print(
        f"总计 {passed}/{total} 通过（每个维度 >= {PASS_THRESHOLD} 分、系统动作未失败，"
        "且 pass_k>1 的用例要求每次都通过，才算通过）"
    )

    for dimension in DIMENSIONS:
        scored = [
            attempt.scores[dimension]
            for result in results if result.error is None
            for attempt in result.attempts
        ]
        if scored:
            average = sum(scored) / len(scored)
            print(f"  {dimension} 平均分: {average:.2f}")

    flaky = [
        result for result in results
        if result.pass_k > 1 and not result.passed
        and any(attempt.passed for attempt in result.attempts)
    ]
    if flaky:
        print()
        print("不稳定用例（部分次数通过、部分失败 —— 不是全错，而是不可靠）：")
        for result in flaky:
            passed_count = sum(a.passed for a in result.attempts)
            print(f"  - {result.case_id}: {passed_count}/{result.pass_k} 次通过")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the LLM-as-judge conversational quality evaluation. "
            "Makes real LLM calls (classification + response generation + "
            "judging) — unlike run_evaluation.py, this is not free and not "
            "fully deterministic."
        )
    )
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
                    "generated_at": datetime.now().isoformat(),
                    "passed": sum(1 for result in results if result.passed),
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
