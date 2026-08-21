from src.backend.graph.state import CustomerServiceState


def reflection_agent_node(
    state: CustomerServiceState,
) -> CustomerServiceState:
    """Recheck conflicts and recommend the next safe workflow route."""
    gate_result = state.get("gate_result")

    if gate_result is None or gate_result.get("status") != "NeedsReflection":
        current_errors = state.get("errors", [])
        return {
            "errors": current_errors
            + ["Reflection Agent 收到不需要 Reflection 的请求"],
        }

    service_request = state.get("service_request")
    order_evidence = state.get("order_evidence")
    logistics_evidence = state.get("logistics_evidence")
    evidence_review = state.get("evidence_review")
    policy_evidence = state.get("policy_evidence")

    required_inputs = {
        "service_request": service_request,
        "order_evidence": order_evidence,
        "logistics_evidence": logistics_evidence,
        "evidence_review": evidence_review,
        "policy_evidence": policy_evidence,
    }
    missing_inputs = [
        name for name, value in required_inputs.items() if value is None
    ]

    if missing_inputs:
        current_errors = state.get("errors", [])
        return {
            "errors": current_errors
            + ["Reflection Agent 缺少输入: " + ", ".join(missing_inputs)],
        }

    request_reason = service_request.get("request_reason")
    policy_ids = set(
        policy_evidence.get("candidate_policy_ids", [])
    )

    if "RISK-001" in policy_ids:
        return {
            "reflection_result": {
                "status": "HumanEscalation",
                "route": "human_escalation",
                "resolved": False,
                "reasons": ["命中高频退款客户复核规则"],
                "required_actions": [
                    "核查历史请求是否集中在短时间",
                    "核查历史退款理由是否重复",
                    "结合本次独立证据进行人工风险审核",
                ],
            }
        }

    if "RISK-004" in policy_ids:
        already_compensated = (
            request_reason == "少发商品"
            and order_evidence.get("confirmed_missing_quantity") == 0
            and (order_evidence.get("already_approved_quantity") or 0) > 0
        ) or (
            request_reason in {"错发商品", "商品损坏", "质量问题"}
            and (order_evidence.get("remaining_eligible_quantity") or 0) <= 0
            and (order_evidence.get("already_approved_quantity") or 0) > 0
        )
        if already_compensated:
            return {
                "reflection_result": {
                    "status": "HumanEscalation",
                    "route": "human_escalation",
                    "resolved": False,
                    "reasons": [
                        f"该商品的「{request_reason}」问题已在此前申请中获得足额赔付，"
                        "本次为同一问题的重复申请"
                    ],
                    "required_actions": [
                        "核对历史售后记录确认已赔付的数量与金额",
                        "如客户反映的是新问题，需人工确认后重新受理",
                    ],
                }
            }

        if request_reason == "未收到货":
            return {
                "reflection_result": {
                    "status": "NeedsExternalVerification",
                    "route": "human_escalation",
                    "resolved": False,
                    "reasons": ["客户未收到主张与系统签收记录冲突"],
                    "required_actions": [
                        "向承运方调取签收凭证",
                        "核对签收人、配送地址和签收时间",
                        "冲突无法消除时由人工作最终判断",
                    ],
                }
            }

        if request_reason == "错发商品":
            return {
                "reflection_result": {
                    "status": "NeedsMoreEvidence",
                    "route": "request_more_evidence",
                    "resolved": False,
                    "reasons": ["客户错发主张与仓库出库商品记录冲突"],
                    "required_actions": [
                        "请求客户提交商品及包裹面单照片",
                        "必要时调取仓库拣货复核记录",
                    ],
                }
            }

        if request_reason == "少发商品":
            return {
                "reflection_result": {
                    "status": "NeedsMoreEvidence",
                    "route": "request_more_evidence",
                    "resolved": False,
                    "reasons": ["客户少发主张与仓库足量出库记录冲突"],
                    "required_actions": [
                        "请求客户提交开箱视频或包裹内容照片",
                        "必要时调取仓库称重和拣货复核记录",
                    ],
                }
            }

    if "REFUND-002" in policy_ids and "SHIPPING-004" in policy_ids:
        return {
            "reflection_result": {
                "status": "HumanEscalation",
                "route": "human_escalation",
                "resolved": False,
                "reasons": ["超时退款条款与历史物流异常同时适用"],
                "required_actions": [
                    "核查历史物流异常是否与当前退款原因存在因果关系",
                    "确认异常解决后客户损失是否已经得到补偿",
                ],
            }
        }

    if "SHIPPING-002" in policy_ids or "SHIPPING-007" in policy_ids:
        return {
            "reflection_result": {
                "status": "NeedsExternalVerification",
                "route": "await_logistics_review",
                "resolved": False,
                "reasons": ["物流轨迹停滞或配送逾期，需要承运商核查包裹状态"],
                "required_actions": [
                    "向承运方发起包裹状态核查",
                    "包裹恢复运输时更新预计送达时间",
                    "承运商确认丢失或核查超时后再进入退款或补发流程",
                ],
            }
        }

    if "REFUND-008" in policy_ids:
        return {
            "reflection_result": {
                "status": "NeedsMoreEvidence",
                "route": "request_more_evidence",
                "resolved": False,
                "reasons": ["现有材料只能确认损坏现象，不能确认损坏原因"],
                "required_actions": [
                    "请求补充完整商品和包装照片或开箱视频",
                    "必要时提交授权售后检测报告",
                ],
            }
        }

    if "REFUND-003" in policy_ids:
        return {
            "reflection_result": {
                "status": "NeedsMoreEvidence",
                "route": "request_more_evidence",
                "resolved": False,
                "reasons": ["现有材料只能确认商品外观异常，不能确认质量责任原因"],
                "required_actions": [
                    "请求补充完整商品和包装照片或开箱视频",
                    "必要时提交授权售后检测报告",
                ],
            }
        }

    return {
        "reflection_result": {
            "status": "HumanEscalation",
            "route": "human_escalation",
            "resolved": False,
            "reasons": ["Reflection 后仍无法形成可靠的自动处理路径"],
            "required_actions": ["由人工复核完整证据链和候选政策"],
        }
    }
