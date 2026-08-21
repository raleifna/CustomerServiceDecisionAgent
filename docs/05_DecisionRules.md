# 规则运行方式

自动裁决规则由 `data/decision_rules.json` 声明，并由
`src/backend/decision/rule_engine.py` 校验和执行。政策 Markdown 负责说明业务含义，
`data/business_rules.json` 保存期限、金额和次数等环境参数，声明式裁决规则负责把
已验证事实映射为决策。规则按 `priority` 从高到低匹配，首次命中即停止。

安全边界仍由 Evidence Completeness Gate 控制：声明式规则只能在 Gate 明确给出
`can_decide=true` 后执行，不能绕过大额人工审核、证据缺失或事实冲突流程。
每次自动裁决会记录 `matched_rule_id`，用于审计实际生效的规则。

可通过 `BUSINESS_RULES_PATH` 和 `DECISION_RULES_PATH` 为不同部署环境指定配置文件。
配置文件加载时会校验规则编号、优先级、条件和支持的操作符，无效配置会明确失败，
不会静默跳过。
