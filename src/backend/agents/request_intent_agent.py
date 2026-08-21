from typing import Any

from src.backend.graph.state import CustomerServiceState
from src.backend.llm import chat_json


VALID_REQUEST_TYPES = {"Refund", "PartialRefund", "Exchange"}
VALID_REQUEST_REASONS = {
    "不想要了",
    "商品损坏",
    "少发商品",
    "未发货取消订单",
    "未收到货",
    "物流延误",
    "质量问题",
    "错发商品",
}

REQUEST_TYPE_LABELS = {
    "Refund": "退款",
    "PartialRefund": "部分退款",
    "Exchange": "换货",
}


def _normalize_intent(raw_intent: dict[str, Any]) -> dict[str, Any]:
    request_type = raw_intent.get("request_type")
    request_reason = raw_intent.get("request_reason")
    requested_quantity = raw_intent.get("requested_quantity")

    if request_type not in VALID_REQUEST_TYPES:
        request_type = None
    if request_reason not in VALID_REQUEST_REASONS:
        request_reason = None
    if (
        not isinstance(requested_quantity, int)
        or isinstance(requested_quantity, bool)
        or requested_quantity <= 0
    ):
        requested_quantity = None

    return {
        "request_type": request_type,
        "request_reason": request_reason,
        "requested_quantity": requested_quantity,
    }


def _format_recent_history(
    history: list[dict[str, str]] | None,
    turns: int = 3,
) -> str:
    """Format the last few dialogue turns for an extraction prompt.

    A multi-turn creation flow ("我想退款" -> "请说明原因" -> "尺码不对")
    only makes full sense with the preceding turns in view.
    """
    if not history:
        return ""
    recent = history[-(turns * 2):]
    if not recent:
        return ""
    lines = [
        f"{'用户' if turn.get('role') == 'user' else 'Agent'}：{turn.get('content', '')}"
        for turn in recent
    ]
    return "最近对话（仅供理解上下文，不要从历史消息里提取字段）：\n" + "\n".join(lines) + "\n\n"


def extract_after_sales_intent(
    user_message: str,
    history: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Extract request_type/request_reason/requested_quantity from free text.

    Classification is semantic, not keyword-matched: phrasings like "尺码不对"、
    "颜色不喜欢"、"拍错了" should all land on request_reason="不想要了" even
    though they share no substring with that label. Raises on LLM failure;
    callers decide how to degrade (see request_intent_node below and
    service_request_action.py for two different fallback strategies).
    """
    system_prompt = (
        "你是电商售后意图抽取器。请根据用户原话输出一个 JSON 对象，"
        "不要进行审批或补充用户未表达的信息。request_type 只能是 Refund、"
        "PartialRefund、Exchange 或 null；request_reason 只能是 不想要了、"
        "商品损坏、少发商品、未发货取消订单、未收到货、物流延误、质量问题、"
        "错发商品 或 null，请按语义归类，不要求用户原话完全匹配这些词——"
        "例如“尺码不对”“颜色不喜欢”“拍错了”“不满意”都属于“不想要了”；"
        "requested_quantity 只能是正整数或 null，用户说的中文数字"
        "（如“两件”“三件”）也需要换算为阿拉伯数字。"
        "如果提供了最近对话，可以用来理解当前消息在说什么，但只提取"
        "最后这一条用户消息里表达的信息。"
    )
    user_prompt = (
        _format_recent_history(history)
        + "请将下面的用户消息提取为 JSON，字段必须是 request_type、"
        f"request_reason、requested_quantity：\n\n{user_message}"
    )
    return _normalize_intent(
        chat_json(
            user_prompt,
            system_prompt=system_prompt,
            temperature=0.0,
        )
    )


def _find_mismatches(
    extracted: dict[str, Any],
    service_request: dict[str, Any],
) -> list[dict[str, Any]]:
    mismatches = []

    for field in ("request_type", "request_reason", "requested_quantity"):
        stated_value = extracted.get(field)
        recorded_value = service_request.get(field)

        if stated_value is not None and stated_value != recorded_value:
            mismatches.append(
                {
                    "field": field,
                    "stated_value": stated_value,
                    "recorded_value": recorded_value,
                }
            )

    return mismatches


def _build_mismatch_response(mismatches: list[dict[str, Any]]) -> str:
    descriptions = []

    for mismatch in mismatches:
        field = mismatch["field"]
        stated = mismatch["stated_value"]
        recorded = mismatch["recorded_value"]

        if field == "request_type":
            stated = REQUEST_TYPE_LABELS.get(stated, stated)
            recorded = REQUEST_TYPE_LABELS.get(recorded, recorded)
            descriptions.append(f"您描述的是{stated}，当前申请记录为{recorded}")
        elif field == "request_reason":
            descriptions.append(f"您描述的问题是“{stated}”，当前记录为“{recorded}”")
        elif field == "requested_quantity":
            descriptions.append(f"您描述的数量是 {stated} 件，当前记录为 {recorded} 件")

    detail = "；".join(descriptions)
    return f"{detail}。为避免处理错误，请确认需要继续处理哪一项。"


def request_intent_node(
    state: CustomerServiceState,
) -> CustomerServiceState:
    """Extract user intent and compare it with the matched service request."""
    user_message = state.get("user_message", "").strip()
    service_request = state.get("service_request")

    if not user_message:
        return {"intent_result": {"status": "Skipped"}}

    if service_request is None:
        return {
            "intent_result": {
                "status": "Unavailable",
                "reason": "MissingServiceRequest",
            }
        }

    try:
        extracted = extract_after_sales_intent(
            user_message, state.get("dialogue_history")
        )
    except Exception as exc:
        return {
            "intent_result": {
                "status": "Unavailable",
                "reason": type(exc).__name__,
            }
        }

    mismatches = _find_mismatches(extracted, service_request)
    if mismatches:
        return {
            "intent_result": {
                "status": "Mismatch",
                "extracted": extracted,
                "mismatches": mismatches,
            },
            "customer_response": _build_mismatch_response(mismatches),
        }

    return {
        "intent_result": {
            "status": "Consistent",
            "extracted": extracted,
            "mismatches": [],
        }
    }
