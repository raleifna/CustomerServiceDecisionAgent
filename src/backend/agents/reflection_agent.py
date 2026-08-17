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

    if "SHIPPING-007" in policy_ids:
        return {
            "reflection_result": {
                "status": "NeedsExternalVerification",
                "route": "await_logistics_review",
                "resolved": False,
                "reasons": ["配送已经逾期，但物流尚未确认丢件"],
                "required_actions": [
                    "向承运方发起包裹状态核查",
                    "确认丢件后再按 SHIPPING-002 处理",
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

    return {
        "reflection_result": {
            "status": "HumanEscalation",
            "route": "human_escalation",
            "resolved": False,
            "reasons": ["Reflection 后仍无法形成可靠的自动处理路径"],
            "required_actions": ["由人工复核完整证据链和候选政策"],
        }
    }
