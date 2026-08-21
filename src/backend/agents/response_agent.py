import os
import re
from typing import Any

from src.backend.graph.state import CustomerServiceState
from src.backend.llm import chat


_MULTI_OPTION_PATTERN = re.compile(r"(?:^|\n)\s*[1-9][.、]\s")


def _format_amount(amount: Any) -> str | None:
    if not isinstance(amount, (int, float)):
        return None

    return f"{float(amount):.2f}".rstrip("0").rstrip(".")


def _build_template_response(final_result: dict[str, Any]) -> str:
    """Build the deterministic response that acts as the safe fallback."""
    handling_status = final_result.get("handling_status")
    decision = final_result.get("decision")
    request_type = final_result.get("request_type")
    request_reason = final_result.get("request_reason")
    approved_quantity = final_result.get("approved_quantity")
    approved_amount = _format_amount(final_result.get("approved_amount"))
    reasons = final_result.get("reasons", [])

    if handling_status == "DecisionCompleted":
        if decision == "Approve":
            if request_type == "Exchange":
                response = "您的换货申请已审核通过"
                if isinstance(approved_quantity, int):
                    response += f"，批准换货数量为 {approved_quantity} 件"
                response += "。后续请按照页面提示完成商品寄回或换货流程。"
            else:
                response = "您的申请已审核通过"
                if isinstance(approved_quantity, int):
                    response += f"，批准数量为 {approved_quantity} 件"
                if approved_amount is not None:
                    response += f"，批准退款金额为 {approved_amount} 元"
                response += "。退款将在后续履约流程完成后按原支付路径处理。"

            if request_reason == "不想要了":
                response += (
                    "无理由退货请保持商品完好、附带原包装退回，"
                    "退款将在平台验收退回商品后处理。"
                )

            execution = final_result.get("execution")
            if execution and execution.get("workflow_status") == "Pending":
                response += "相关履约任务已经创建，执行结果将另行更新。"

        elif decision == "PartialApprove":
            response = "您的申请已完成审核，本次仅能批准部分申请"
            if isinstance(approved_quantity, int):
                response += f"，批准数量为 {approved_quantity} 件"
            if approved_amount is not None:
                response += f"，批准退款金额为 {approved_amount} 元"
            response += "。其余部分暂不符合当前可确认的处理范围。"

        elif decision == "Reject":
            response = "很抱歉，您的申请未能通过审核"
            if reasons:
                response += f"，原因是：{reasons[0]}"
            response += "。如您对结果有异议，可联系人工客服进一步核查。"

        elif decision == "NoActionRequired":
            response = "您的咨询已完成核查，目前不需要执行退款或换货操作。"

        else:
            response = "您的申请已经完成审核，请查看当前申请的处理结果。"

    elif handling_status == "HumanEscalation":
        response = (
            "您的申请需要进一步人工审核。系统已保留订单、证据和政策核查结果，"
            "人工客服将基于完整信息继续处理。"
        )

    elif handling_status == "NeedsMoreEvidence":
        if request_reason == "错发商品":
            response = (
                "目前的信息尚不足以确认错发情况，请补充您实际收到的商品、"
                "外包装和快递面单的清晰照片。"
            )
        elif request_reason == "少发商品":
            response = (
                "目前的信息尚不足以确认少发情况，请补充开箱视频，"
                "或能够清晰展示包裹内容和数量的照片。"
            )
        elif request_reason == "商品损坏":
            response = (
                "目前的信息只能确认商品存在损坏现象，请补充完整商品、"
                "包装情况的照片或开箱视频；必要时可提供售后检测报告。"
            )
        elif request_reason == "质量问题":
            response = (
                "目前尚缺少可用于审核质量问题的有效材料，请补充能够识别商品"
                "并展示问题的清晰图片、完整操作视频或售后检测报告。"
            )
        else:
            response = "目前的信息尚不足以完成审核，请补充相关证明材料。"

    elif handling_status == "AwaitingEvidenceReview":
        response = (
            "您提交的证明材料仍在审核中，目前暂时无法形成最终结论。"
            "材料审核完成后可继续处理本次申请。"
        )

    elif handling_status == "AwaitingExternalVerification":
        response = (
            "当前订单信息仍需要向物流服务方进一步核查。"
            "在包裹状态确认前，暂时无法完成退款判断。"
        )

    elif handling_status == "SystemError":
        response = (
            "当前系统暂时无法完成申请核查，请稍后重试或联系人工客服。"
        )

    else:
        response = "您的申请正在处理中，请稍后查看最新进度。"

    return response


def _llm_response_enabled() -> bool:
    return os.getenv("ENABLE_LLM_RESPONSE", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _contains_required_facts(
    polished_response: str,
    template_response: str,
    final_result: dict[str, Any],
) -> bool:
    """Reject an LLM response if it drops or changes important business facts."""
    if not polished_response:
        return False

    approved_quantity = final_result.get("approved_quantity")
    approved_amount = _format_amount(final_result.get("approved_amount"))

    if isinstance(approved_quantity, int) and str(approved_quantity) not in polished_response:
        return False

    if approved_amount is not None and approved_amount not in polished_response:
        return False

    handling_status = final_result.get("handling_status")
    decision = final_result.get("decision")
    request_reason = final_result.get("request_reason")
    required_keywords: tuple[str, ...] = ()

    if handling_status == "DecisionCompleted":
        if decision == "Approve":
            required_keywords = ("通过", "批准")
            if request_reason == "不想要了" and not any(
                keyword in polished_response
                for keyword in ("验收", "完好", "原包装")
            ):
                return False
        elif decision == "PartialApprove":
            required_keywords = ("部分",)
        elif decision == "Reject":
            required_keywords = ("未能通过", "无法通过", "暂不支持", "拒绝")
        elif decision == "NoActionRequired":
            required_keywords = ("不需要", "无需")
    elif handling_status == "HumanEscalation":
        required_keywords = ("人工",)
    elif handling_status == "NeedsMoreEvidence":
        required_keywords = ("补充", "提供")
    elif handling_status == "AwaitingEvidenceReview":
        required_keywords = ("审核",)
    elif handling_status == "AwaitingExternalVerification":
        required_keywords = ("核查", "确认")
    elif handling_status == "SystemError":
        required_keywords = ("系统", "稍后", "人工")

    if required_keywords and not any(
        keyword in polished_response for keyword in required_keywords
    ):
        return False

    # Caught in the wild: a length-only check let a "here are 3 versions,
    # pick one" reply through once (multiple short numbered options can add
    # up to under the length cap even though it's clearly not one clean
    # customer-facing answer).
    if _MULTI_OPTION_PATTERN.search(polished_response):
        return False

    # A polished message should remain close to the concise deterministic template.
    return len(polished_response) <= max(len(template_response) * 3, 200)


def _format_recent_history(
    history: list[dict[str, str]] | None,
    turns: int = 3,
) -> str:
    """Format the last few dialogue turns so a polish pass can acknowledge
    context (a pivot from "催发货" to "退了吧") instead of reading like it
    forgot the conversation just happened.
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
    return "最近对话：\n" + "\n".join(lines) + "\n\n"


def _polish_response(
    template_response: str,
    final_result: dict[str, Any],
    dialogue_history: list[dict[str, str]] | None = None,
) -> str:
    """Polish wording with the LLM and safely fall back to the template."""
    if not _llm_response_enabled():
        return template_response

    system_prompt = (
        "你是电商售后客服回复编辑器。你只能润色用户可见的客服话术，"
        "不能改变审批结论、金额、数量、处理状态或下一步动作，不能添加承诺，"
        "不能声称退款已经到账，也不能暴露内部规则、风控信息或系统推理。"
        "如果提供了最近对话，可以让措辞自然地承接对话（比如客户中途改了主意，"
        "可以顺带确认一下），但不能编造对话里没有的事实。"
        "避免逐项罗列的机器人报告体，改写成一两句连贯自然的话，像人在说话；"
        "请保持简洁、自然、专业，只输出润色后的回复正文。"
    )
    user_prompt = (
        _format_recent_history(dialogue_history)
        + f"请润色下面的客服回复，保留全部事实：\n\n{template_response}"
    )

    try:
        polished_response = chat(
            user_prompt,
            system_prompt=system_prompt,
            temperature=0.2,
        )
    except Exception:
        return template_response

    if not _contains_required_facts(
        polished_response,
        template_response,
        final_result,
    ):
        return template_response

    return polished_response


def response_agent_node(
    state: CustomerServiceState,
) -> CustomerServiceState:
    """Generate a safe customer-facing response with optional LLM polishing."""
    final_result = state.get("final_result")

    if final_result is None:
        current_errors = state.get("errors", [])
        return {
            "errors": current_errors + ["Response Agent 缺少 final_result"],
        }

    template_response = _build_template_response(final_result)
    customer_response = _polish_response(
        template_response, final_result, state.get("dialogue_history")
    )

    # Intake and adjudication can happen in the same graph turn.  Do not lose
    # the successful write acknowledgement when the newly-created request is
    # then routed to evidence collection or human review.
    action_result = state.get("action_result") or {}
    if (
        action_result.get("action") == "CreateServiceRequest"
        and action_result.get("status") == "Created"
    ):
        request_id = action_result.get("request_id") or final_result.get("request_id")
        acknowledgement = f"已为订单创建售后申请（申请编号：{request_id}）。"
        customer_response = acknowledgement + customer_response

    return {"customer_response": customer_response}
