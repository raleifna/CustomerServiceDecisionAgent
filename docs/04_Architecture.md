# Architecture Design
# CustomerServiceDecisionAgent

> 本文档描述系统如何实现 [01_PRD.md](./01_PRD.md) 中定义的业务能力。当前版本用于约束首版代码的组件边界与最小接口；具体类名、Prompt、State 字段和 Conditional Edge 在代码跑通后补充到 `docs/06_LowLevelDesign.md`，不在实现前过度固定。

---

# 1. 架构目标与边界

## 1.1 核心目标

系统处理的对象是 **Service Request**，而不是订单本身或一段孤立对话。订单、客户、商品、仓库出库、物流、申请材料和政策都是围绕该请求收集的证据。

```text
Customer Request（场景化输入 / 实时对话）
       │ 识别诉求 / request_id
       ▼
Service Request
       │
       ├── Enterprise Facts：Order / Customer / Product / Logistics
       ├── Submitted Evidence：Image / Video / Diagnostic Report（结构化审核结果）
       └── Policy Evidence：Refund / Shipping / VIP / Risk
                         │
                         ▼
                Evidence Fusion Decision
                         │
                    Reflection / Human
```

系统需要做到：

- 将客户主张与已经验证的企业事实、申请材料分开；
- 让各证据来源输出结构化、可追溯的结果；
- 使用显式业务规则、冲突检测和置信度完成裁决；
- 对缺证据、证据冲突和高风险请求进入 Reflection 或人工升级；
- 保存决策依据、命中政策和执行轨迹，支持 Evaluation。

## 1.2 当前业务边界

首版聚焦订单创建后的服务：退款、换货、部分退款、商品质量问题、错发/少发、物流异常和价保。售前推荐、优惠券发放、在线支付和普通闲聊不属于核心决策范围。

## 1.3 为什么不是单 Agent + 工具调用

单个 Agent 同时负责查数据、理解政策和决定结果时，会产生以下问题：

- 信息收集与裁决逻辑耦合，难以分别测试；
- 客户主张容易被误当成已验证事实；
- 不同来源冲突时，模型可能通过自然语言掩盖冲突；
- 无法稳定评估工具调用、证据完整性和规则命中情况。

因此系统将请求规划、证据收集、政策检索、确定性裁决、复核和回复分开编排。这里的“Agent”表示具有明确输入输出边界的任务节点，不要求所有节点都由 LLM 实现；事实查询和硬规则判断优先采用确定性代码。

## 1.4 Agent 拆分原则

证据收集节点按数据责任域拆分：

- Order Agent：订单及其关联的客户、商品和仓库出库事实；
- Logistics Agent：独立物流异常事实；
- Evidence Agent：客户材料或专业报告的结构化审核结果；
- Policy Agent：适用于当前请求的企业政策；
- Decision Engine：只融合证据和执行规则，不直接访问原始数据文件。

Order Agent 首版聚合读取 Orders、Customers 和 Products，是为了控制 Agent 数量；其输出中必须保留每个事实的来源表。未来接入独立 CRM 或商品服务时，可再拆成 Customer Agent 与 Product Agent，而不改变 Decision Engine 的输入协议。

---

# 2. 系统分层

```text
┌──────────────────────────────────────────────────────────────┐
│ Interface Layer                                              │
│ Streamlit / FastAPI：接收对话、request_id，展示处理轨迹       │
└──────────────────────────────┬───────────────────────────────┘
                               ▼
┌──────────────────────────────────────────────────────────────┐
│ Orchestration Layer                                          │
│ LangGraph：Planner、并行证据收集、条件路由、状态/checkpoint    │
└──────────────────────────────┬───────────────────────────────┘
                               ▼
┌──────────────────────────────────────────────────────────────┐
│ Decision Layer                                               │
│ Decision Engine → Reflection → Response / Human Escalation   │
└──────────────────────────────┬───────────────────────────────┘
                               ▼
┌──────────────────────────────────────────────────────────────┐
│ Data & Knowledge Layer                                       │
│ JSON Mock DB / Markdown Knowledge Base / Evaluation Cases    │
└──────────────────────────────────────────────────────────────┘
```

自动裁决不会直接声称退款、换货或取消已经完成。Finalization 在同一个数据库事务中
写入不可变 `decision_records`、幂等 `action_tasks` 和 `outbox_events`，随后由独立
Worker 调用支付、仓储或订单系统。业务申请在任务完成前保持
`ExecutionPending`，外部执行成功后才能关闭。

数据层的具体 Schema 以 [02_DataDesign.md](./02_DataDesign.md) 为准。

---

# 3. LangGraph 逻辑流程

```text
                         Customer Request
                                │
                                ▼
                     Request Resolver / Planner
                  （解析或校验 request_id 与诉求）
                                │
                   ┌────────────┴────────────┐
                   │ 请求无效/信息不足        │ 请求有效
                   ▼                         ▼
              Clarification            Evidence Collection
                                  ┌────────┼───────────┬─────────┐
                                  ▼        ▼           ▼         ▼
                               Order   Logistics   Evidence   Policy
                               Agent     Agent       Agent     Agent
                                  └────────┼───────────┴─────────┘
                                           ▼
                               Evidence Completeness Gate
                                  │                    │
                            足够且可裁决          缺失/待审核/冲突
                                  │                    │
                                  ▼                    ▼
                             Decision Engine      Reflection Agent
                                  │                    │
                                  └─────────┬──────────┘
                                            ▼
                              Decision Finalization Gate
                                  │                    │
                              可自动完成             仍不可靠/硬升级
                                  ▼                    ▼
                            Response Agent      Human Escalation
```

首版允许 Planner 为所有核心请求统一调度四个证据 Agent，以保证实现简单和证据完整；后续再根据 `request_type` 优化不必要的调用。

---

# 4. 组件职责

## 4.1 Request Resolver / Planner

**对应能力**：PRD FR1 Task Planning

职责：

- 从 API 参数或对话中取得 `request_id`；
- 查询并校验 Service Request 是否存在；
- 校验请求关联的 `order_id` 和 `customer_id`；
- 当用户尚未提供必要标识时生成澄清问题；
- 规划需要执行的证据收集节点。

不做：

- 不把“商品坏了”等客户陈述标记为已验证事实；
- 不检索政策、不计算最终置信度、不批准或拒绝请求。

首版 Demo 以已经存在于 `service_requests.json` 的 `request_id` 为入口。未来实时会话中，可在意图和关联订单确认后创建新的 Service Request。

## 4.2 Order Agent

**数据源**：`service_requests.json`、`orders.json`、`customers.json`、`products.json`

职责：

- 查询请求及关联订单；
- 计算签收状态、延迟天数和申请距签收天数；
- 读取 `delivery_recipient_type`，识别“本人签收但客户称未收到”等冲突信号；
- 比较 `product_id` 与 `shipped_product_id` 判断仓库错发；
- 比较 `quantity` 与 `shipped_quantity` 判断少发或多发；
- 提供金额、商品属性、会员等级、注册日期和退款历史等事实；
- 为每个事实标注来源表和来源记录 ID。

不做：不读取物流异常、不读取申请材料、不检索政策、不作最终裁决。

## 4.3 Logistics Agent

**数据源**：`logistics.json`，必要时使用 Order Agent 已计算的生命周期信息

职责：

- 查询与 `order_id` 关联的物流异常；
- 输出异常类型、记录日期和是否解决；
- 明确区分“没有异常记录”与“已证明没有异常”；
- 将物流事实转换成统一证据格式。

不做：不从客户对话猜测异常原因，不把 `resolved=true` 自动解释为客户损失已经得到补偿。

## 4.4 Evidence Agent

**数据源**：`service_request_evidence.json`

当前版本读取的是图片、视频或检测报告已经结构化后的审核结果，不读取真实媒体文件，也不负责鉴定伪造或损坏责任。

最小输入：

```json
{
  "request_id": "SR2026080005"
}
```

最小输出：

```json
{
  "request_id": "SR2026080005",
  "evidence_available": true,
  "evidence_sufficient": true,
  "verification_statuses": ["MachineReviewed"],
  "verified_facts": [
    "视频中的商品类别与订单商品一致",
    "商品在完整启动操作后没有正常启动"
  ],
  "confidence": 0.88,
  "requires_human_review": false,
  "source_ids": ["EVD2026080005"]
}
```

确定性处理原则：

- 存在 `NeedsHumanReview` → `requires_human_review=true`；
- 只有 `Pending` 或 `Rejected` → 不得判为证据充分；
- `Verified` 可作为高置信度事实；
- `MachineReviewed` 是否充分由证据类型、最低材料要求和阈值共同决定；
- 没有证据记录时返回 `evidence_available=false`，不编造材料。

## 4.5 Policy Agent

**数据源**：`refund_policy.md`、`shipping_policy.md`、`vip_policy.md`、`risk_policy.md`

职责：

- 根据请求类型、原因和已收集事实检索候选条款；
- 返回稳定政策编号、相关片段、适用条件和检索置信度；
- 同时保留可能冲突的条款，不提前替 Decision Engine 选择唯一答案。

不做：不修改 Service Request，不把检索相似度当成最终决策置信度。

## 4.6 Evidence Completeness Gate

该节点使用确定性规则检查：

- 必需 Agent 是否成功返回；
- 外键关联是否完整；
- 质量问题是否达到最低证据要求；
- 是否存在 `Pending`、`NeedsHumanReview` 或相互矛盾的事实；
- Policy Agent 是否返回可引用的条款编号。

缺少可补充信息时进入 Clarification 或 Reflection；硬性人工条件则直接进入 Human Escalation。节点失败与“查询结果为空”必须区分记录。

## 4.7 Evidence Fusion Decision Engine

**对应能力**：PRD FR3 Decision Analysis

Decision Engine 是确定性规则优先、模型辅助解释的裁决组件，而不是一个自由回答问题的 LLM。

```text
Enterprise Facts
      + Submitted Evidence
      + Policy Hits
      + Risk Signals
      ─────────────────────► Rule Evaluation
                                │
                                ▼
                   Decision + Confidence + Reason Chain
```

规则优先级采用政策中已经约定的方向：

```text
人工升级规则
    > 风险复核规则
    > 安全例外
    > 专项售后规则
    > 常规时效规则
```

输出至少包含：

```json
{
  "request_id": "SR2026080005",
  "proposed_decision": "Approve",
  "decision_confidence": 0.86,
  "rule_hits": ["REFUND-003"],
  "supporting_evidence_ids": ["EVD2026080005"],
  "conflicts": [],
  "reflection_required": false,
  "human_escalation_required": false,
  "reason_chain": []
}
```

`proposed_decision` 是进入最终状态写回前的候选结论。置信度计算、权重和阈值由 `docs/05_DecisionRules.md` 定义，Architecture 不重复硬编码数值。

## 4.8 Reflection Agent

**对应能力**：PRD FR4 Result Verification

任一条件命中即进入 Reflection：

| 类型 | 示例 |
|---|---|
| Low Confidence | 综合置信度低于配置阈值 |
| Missing Evidence | 质量问题只有客户陈述，缺少最低材料 |
| Evidence Conflict | 客户声称少发，但仓库记录显示足量出库 |
| Rule Conflict | 超过30天拒绝条款与历史物流异常例外同时命中 |
| Risk Trigger | 高频退款客户，即使本次证据充分仍需复核 |

Reflection 的职责是重新检查证据完整性、冲突和规则适用性，可请求补充证据或重新执行失败的查询。它不能伪造新事实。

以下情况不应停留在普通 Reflection，而应直接或最终转人工：

- 命中大额强制人工规则；
- Evidence Agent 返回 `NeedsHumanReview`；
- Reflection 后关键冲突仍无法消除；
- 必需数据源持续不可用。

VIP 身份本身不是风险触发器。VIP 只影响服务优先级或政策权益；只有与大额、身份冲突或其他风险信号组合时才复核或升级。

## 4.9 Response Agent

**对应能力**：PRD FR5 Response Generation

职责：

- 仅根据最终决策或当前处理状态生成客户回复；
- 清晰说明批准、拒绝、补材料、复核中或已转人工；
- 使用客户可理解的关键依据，不暴露内部风险分数或敏感风控策略；
- 不改变 Decision Engine 的结论和金额。

## 4.10 Human Escalation

**对应能力**：PRD FR6 Human Escalation

转人工载荷至少包含：

- `request_id`、`order_id`、`customer_id`；
- 客户诉求摘要；
- 企业事实和申请证据摘要；
- 命中的政策与风险规则；
- 冲突点、缺失项和已尝试操作；
- Decision Engine 的候选结论（如有），但不得伪装成人工最终结论。

---

# 5. 统一证据协议

各证据 Agent 不应只返回自由文本。首版统一协议建议为：

```json
{
  "agent": "OrderAgent",
  "request_id": "SR2026080006",
  "status": "Success",
  "facts": [
    {
      "field": "shipment.product_match",
      "value": false,
      "source_type": "WarehouseRecord",
      "source_id": "ORD2026080006",
      "confidence": 1.0
    }
  ],
  "warnings": [],
  "error": null
}
```

`status` 建议使用：

```text
Success / NoData / InvalidInput / SourceUnavailable / Error
```

其中 `NoData` 是有效查询结果，例如订单没有物流异常记录；`SourceUnavailable` 才表示数据源不可用。Decision Engine 不得把二者混淆。

---

# 6. Graph State 最小契约

代码实现前只固定跨节点共享的稳定字段：

```text
request_id
dialogue_context
service_request
plan
order_evidence
logistics_evidence
submitted_evidence
policy_evidence
proposed_decision
reflection_result
final_decision
workflow_status
errors
trace
```

State 中不放 Evaluation Ground Truth。`case_id`、`expected_decision` 和 `expected_policy_hits` 只存在于评估层，避免标签泄漏。

---

# 7. 状态写回与副作用边界

证据收集和候选决策阶段均为只读。只有 Finalization 节点可以更新 `service_requests.json`：

| 处理结果 | workflow_status | decision |
|---|---|---|
| 等待处理 | `Pending` | `null` |
| 正在自动处理 | `InReview` | `null` 或候选结果不写回 |
| 需要复核 | `NeedsReflection` | `null` |
| 强制人工 | `EscalatedToHuman` | `null` |
| 自动决策完成 | `Closed` | `Approve` / `Reject` / `PartialApprove` / `NoActionRequired` |

退款到账、换货出库等履约执行状态不属于当前 `workflow_status`。首版 Decision Engine 只作决策，不直接调用支付、退款或仓库执行接口。

---

# 8. 技术选型

| 模块 | 选型 | 理由与边界 |
|---|---|---|
| 编排 | LangGraph | 显式节点、条件边、并行证据收集和 checkpoint；首版以实际代码验证具体图结构 |
| 模型接口 | OpenAI-compatible SDK + 可配置模型 | 隔离模型供应商；结构化查询和规则判断不依赖 LLM |
| API | FastAPI | 提供请求处理、状态查询和调试接口 |
| Demo | Streamlit | 展示证据、规则命中、Reflection 和最终结果 |
| Knowledge Base | Markdown；检索后端可替换 | 首版可先做编号/关键词检索，再接 ChromaDB，避免 RAG 阻塞核心流程 |
| Mock Database | JSON + Repository 层 | JSON 不由 Agent 直接遍历，通过数据访问层查询和校验 |
| 配置 | 独立规则/设置文件 | 阈值、权重和评估日期不得散落在 Prompt 中 |

---

# 9. 数据来源与用途

| 数据资产 | 用途 | 是否决定 Ground Truth |
|---|---|---:|
| Enterprise Mock Database | 提供订单、客户、商品、物流和申请证据事实 | 是，作为事实部分 |
| Knowledge Base | 提供退款、物流、VIP 和风险条款 | 是，作为规则部分 |
| 场景驱动 Evaluation Cases | 保存预期决策、规则命中和轨迹 | 是 |
| ECD 筛选/适配对话 | 提供真实口语表达和鲁棒性测试素材 | 否 |
| CrossWOZ | 参考角色、intent 和 slot 标注思想 | 否 |

真实电商对话不是 Agent 运行的前置依赖。核心评估由可控的 Mock Facts、Policies 和 Evaluation Ground Truth 驱动；真实对话用于验证口语、缺信息、多轮澄清和表达变化。

---

# 10. 首版 Demo 路径

首版至少展示四种不同路径：

```text
路径 A：证据充分 → 自动批准
SR2026080005 → Order + Evidence + Policy → REFUND-003 → Approve

路径 B：明确超时且无例外 → 自动拒绝
SR2026080003 → Order + Logistics(NoData) + Policy → REFUND-002 → Reject

路径 C：政策或风险冲突 → Reflection
SR2026080004 / SR2026080009 → 冲突或风险信号 → NeedsReflection

路径 D：硬性人工条件 → Human Escalation
SR2026080010 → 大额 + 新账号 + 材料需人工 → EscalatedToHuman
```

Demo 应展示：

- 每个 Agent 的查询状态与来源 ID；
- 收集到的事实与证据置信度；
- 命中的政策编号；
- 冲突与风险触发器；
- 最终状态和客户回复；
- 完整 trace，而不只展示最终答案。

---

# 11. 实现后待补内容

以下内容应在代码跑通后根据真实实现补入，而不是现在猜测：

- LangGraph 的具体 State 类型和 reducer；
- 并行节点的实际 fan-out/fan-in 实现；
- Agent Prompt 与 structured output model；
- Repository、Tool 和 Agent 的具体类/函数路径；
- 重试、超时和 checkpoint 后端；
- `docs/05_DecisionRules.md` 中的最终权重与阈值；
- API 请求/响应 Schema；
- Evaluation 中的 tool trajectory 与节点级指标。
