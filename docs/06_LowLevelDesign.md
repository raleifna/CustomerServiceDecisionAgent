# Low-Level Design
# CustomerServiceDecisionAgent

> 本文档描述 [04_Architecture.md](./04_Architecture.md) 中概念角色对应的真实 LangGraph 节点、
> State 字段、Prompt 与 Conditional Edge。概念名（如"Planner"）与代码节点名不是一一对应，
> 以本文档的节点名和文件路径为准。

---

# 1. Graph State 契约

完整定义见 `src/backend/graph/state.py`（`CustomerServiceState`，`TypedDict, total=False`）：

```python
user_message: str                          # 本轮客户原始输入
conversation_context: dict[str, Any]       # 跨轮次会话上下文（customer_id、active_order_id、pending_action 等）
conversation_result: dict[str, Any]        # Router 的意图判定结果
dialogue_history: list[dict[str, str]]     # 最近对话轮次，供 Prompt 拼接上下文
resolver_result: dict[str, Any]            # Request Resolver 对已有 request_id 的解析结果
intent_result: dict[str, Any]              # Request Intent Agent 抽取的 request_type/reason/quantity
query_result: dict[str, Any]               # Query Agent 的结构化查询结果
action_result: dict[str, Any]              # 各 Action 节点的执行结果（Created/Existing/Failed 等）

request_id, service_request, order, order_items, request_items, order_item, customer, product
logistics, submitted_evi                   # 各数据 Agent 的原始输入（由上游 load_* 节点填充）

order_evidence, logistics_evidence         # Order/Logistics Agent 产出的结构化证据
evidence_review                            # Evidence Agent 产出的证据审核汇总
policy_evidence                            # Policy Agent 产出的候选政策
gate_result                                # Evidence Completeness Gate 的裁决前置检查结果
proposed_decision                          # Decision Engine 的候选裁决
reflection_result                          # Reflection Agent 的复核结果
final_result                               # Finalization 归一化后的最终结果（写回 Postgres）
customer_response: str                     # 最终展示给客户的文本

errors: list[str]                          # 累积的节点级错误，任一非空即视为异常路径
```

State 中不保存 Evaluation Ground Truth（`case_id`、`expected_decision` 等只存在于
`data/evaluation/`，避免标签泄漏进真实运行路径）。

---

# 2. 节点清单（真实 LangGraph 节点名）

节点在 `src/backend/graph/workflow.py` 的 `workflow_builder.add_node(...)` 处注册，
函数实现分布在 `src/backend/agents/`、`src/backend/decision/`、`src/backend/actions/`、
`src/backend/graph/` 四个目录。

| 节点名 | 函数 | 文件 | 职责 |
|---|---|---|---|
| `prepare_turn` | `prepare_turn_node` | `graph/workflow.py` | 归一化本轮输入、合并会话上下文 |
| `conversation_router` | `conversation_router_node` | `agents/conversation_router_agent.py` | 意图分类；处理待确认 Offer（催发货/催物流/转人工）的 yes/no 判定 |
| `request_resolver` | `request_resolver_node` | `agents/request_resolver_agent.py` | 从用户消息中解析已存在的 `request_id` |
| `load_service_request` | `load_service_request_node` | `graph/workflow.py` | 按 `request_id` 加载 Service Request 记录 |
| `request_intent` | `request_intent_node` | `agents/request_intent_agent.py` | 从自然语言抽取 `request_type`/`request_reason`/`requested_quantity` |
| `service_request_action` | `service_request_action_node` | `actions/service_request_action.py` | 校验并创建/复用 Service Request；处理 30 天提交上限、二次驳回强制转人工等前置拦截 |
| `load_order` / `load_order_items` / `load_customer` / `load_product` | 同名 `*_node` | `graph/workflow.py` | 按 `order_id` 依次加载订单头、订单项、客户、商品 |
| `order_agent` | `order_agent_node` | `agents/order_agent.py` | 计算签收状态、延迟天数、错发/少发、已批准数量扣减 |
| `logistics_agent` | `logistics_agent_node` | `agents/logistics_agent.py` | 汇总物流异常事实（停滞/滞留/是否已解决） |
| `evidence_agent` | `evidence_agent_node` | `agents/evidence_agent.py` | 汇总客户提交材料的审核状态 |
| `policy_agent` | `policy_agent_node` | `agents/policy_agent.py` | 按事实匹配候选政策编号与风险信号（RISK-001~004） |
| `evidence_completeness_gate` | `evidence_completeness_gate_node` | `graph/evidence_gate.py` | 裁决前置检查：证据是否齐全、是否强制人工、是否直接可裁决 |
| `decision_engine` | `decision_engine_node` | `decision/engine.py` | 调用声明式规则引擎产出候选裁决 |
| `reflection_agent` | `reflection_agent_node` | `agents/reflection_agent.py` | 复核冲突/风险信号，给出下一步路由 |
| `finalization` | `finalization_node` | `graph/finalization.py` | 归一化裁决/复核结果，写回 Postgres，创建履约任务 |
| `response_agent` | `response_agent_node` | `agents/response_agent.py` | 生成客户可读回复（模板 + 可选 LLM 润色） |
| `query_agent` | `query_agent_node` | `agents/query_agent.py` | 订单状态/物流查询（Tool-calling，非决策路径） |
| `service_request_status_query` | `service_request_status_query_node` | `agents/query_agent.py` | 售后进度查询 |
| `fulfillment_action` | `fulfillment_action_node` | `actions/fulfillment_action.py` | 幂等创建"催发货"业务事件 |
| `logistics_chase_action` | `logistics_chase_action_node` | `actions/logistics_chase_action.py` | 幂等创建"催物流"业务事件 |
| `human_handoff_action` | `human_handoff_action_node` | `actions/human_handoff_action.py` | 创建人工转接记录 |
| `withdraw_service_request_action` | `withdraw_service_request_action_node` | `actions/withdraw_service_request_action.py` | 客户主动撤销申请 |
| `record_turn` | `record_turn_node` | `graph/workflow.py` | 落盘对话历史，收尾本轮 |

---

# 3. Conditional Edge（真实路由函数）

全部定义在 `src/backend/graph/workflow.py`，函数名即路由名：

## 3.1 `route_after_conversation_router`

```text
intent == OrderStatusQuery/ShippingTimeQuery/LogisticsQuery  → query_agent
intent == AfterSalesStatusQuery                                → request_resolver
intent == AfterSalesRequest
    且 matched_by ∈ {reapplication_cancelled, pending_offer_declined} → record_turn（不建单）
    且已有 request_id 且本轮无新消息                                  → request_resolver
    否则                                                              → service_request_action
intent == ExpediteShipmentConfirmation                          → fulfillment_action
intent == LogisticsChaseConfirmation                            → logistics_chase_action
intent == HumanHandoff                                           → human_handoff_action
intent == WithdrawServiceRequest                                 → withdraw_service_request_action
其他                                                              → end
```

`HumanHandoff` 意图既可由客户直接说"转人工"触发，也可由 `service_request_action`
在命中 30 天提交上限时通过 `pending_action = "OfferHumanHandoff"` 挂起，等客户确认后
在下一轮触发（见 §5.3）。

## 3.2 `route_after_service_request_action`

```text
action_result.status ∈ {Created, Existing} → load_order（复用后续证据收集 + 裁决管线，
                                               而不是对"看起来没问题"的申请走捷径，
                                               确保 RISK-002 等强制人工规则不会被绕过）
其他（如 Failed）                            → record_turn
```

## 3.3 `route_after_evidence_gate`

直接读取 `gate_result.route`（由 `evidence_gate.py` 显式给出，见 §4.6），
`gate_result is None` 时兜底为 `end`。

## 3.4 其余装载类路由

`route_after_request_intent`、`route_after_request_resolver`、`route_after_order`、
`route_after_order_items`、`route_after_customer` 都是"必需数据缺失即提前终止"的
简单判空路由，避免用 `SystemError` 掩盖"这一步本该有数据但没有"和"这一步本来就
没有数据"两种不同情况。

---

# 4. 关键节点详细设计

## 4.1 Order Agent（`agents/order_agent.py`）

**输入**：`order`、`order_item`、`service_request`
**输出**（`order_evidence`）：`product_match`（`True`/`False`/`None`=未发货）、
`quantity_match`、`confirmed_missing_quantity`、`is_delivered`、`days_since_delivery`、
`delivery_recipient_type` 等。

**关键设计**：数量类裁决必须扣减"同一订单项 + 同一 request_reason 此前已批准的数量"
（`get_already_approved_quantity`），因为唯一索引 `uq_open_service_request_item` 只
挡得住并发重复的*未关闭*申请，挡不住"上次已经批准关闭、这次又提交一笔新的"这种
情况，需要在业务逻辑层显式扣减已批准数量。

## 4.2 Logistics Agent（`agents/logistics_agent.py`）

**输入**：`logistics`（该订单的物流异常记录列表）
**输出**（`logistics_evidence`）：`has_logistics_exception`、`unresolved_exception_types`、
`has_unresolved_tracking_stalled`（`exception_type == "物流停滞超72小时"` 且未解决）。

订单不在异常表中 = "无异常记录"，不等于"已证明无异常"；这个区分由上游数据设计保证
（见 `docs/02_DataDesign.md` 2.2 节 Logistics 表），Logistics Agent 本身不做推断。

## 4.3 Evidence Agent（`agents/evidence_agent.py`）

**输入**：`submitted_evi`（该申请的证据记录列表，`verification_status` 取值见
`docs/02_DataDesign.md` 2.2 节）
**输出**（`evidence_review`）：`has_usable_evidence`（`Verified` 或 `MachineReviewed`
即可）、`has_machine_reviewed_evidence`、`has_verified_evidence`、`has_pending_evidence`、
`has_rejected_evidence`、`requires_human_review`（存在 `NeedsHumanReview` 即为真）。

`has_usable_evidence` 只回答"有没有材料能用"，不回答"能不能自动裁决"——后者由
Evidence Gate 结合 `request_reason` 和 `has_machine_reviewed_evidence` 再判一次
（见 §4.6）。

## 4.4 Policy Agent（`agents/policy_agent.py`）

**输入**：`service_request`、`order_evidence`、`logistics_evidence`、`evidence_review`、
`data/business_rules.json`
**输出**（`policy_evidence.candidate_policy_ids`）：确定性匹配得到的候选政策编号列表
（如 `REFUND-003`、`RISK-001`），每个编号附带 `selection_reasons`。

匹配逻辑是显式的 `if` 判断 + `add_candidate(policy_id, reason)`，不是向量检索——
见 `docs/02_DataDesign.md` 3.1 节的说明。风险信号部分：

```text
RISK-001  历史退款次数 >= high_refund_count
RISK-002  订单金额 >= high_value_amount
RISK-003  新账号（注册 <= new_account_days 天）+ 首次退款 + 金额 >= high_value_amount
RISK-004  客户"错发"主张与仓库出库记录冲突
```

RISK-003 的强制人工检查在 Evidence Gate 中独立实现（不依赖 RISK-002 恰好同时命中），
见 §4.6。

## 4.5 Request Intent Agent（`agents/request_intent_agent.py`）

用 LLM 从自然语言中抽取 `request_type`/`request_reason`/`requested_quantity`，
`_find_mismatches` 会与已有 `service_request` 记录比对，识别"用户这轮改口了"的情况
而不是静默覆盖。

## 4.6 Evidence Completeness Gate（`graph/evidence_gate.py`）

确定性检查链，按顺序短路：

```text
1. 必需输入是否齐全 / 已有 errors                → SystemError → end
2. 候选政策是否有加载失败                          → SystemError → end
3. 证据要求人工审核 或 命中 RISK-002/RISK-003      → HumanEscalation → human_escalation
4. 存在 Pending 证据                              → AwaitingEvidenceReview → await_review
5. 质量问题/商品损坏 但无可用证据                  → NeedsMoreEvidence → request_more_evidence
6. 命中 RISK-001/RISK-004/超时物流冲突/配送逾期未确认丢件 → NeedsReflection → reflection
7. REFUND-003/REFUND-008 命中但只有 MachineReviewed、无 Verified → NeedsReflection → reflection
8. 候选政策不在可自动裁决集合内                     → NeedsReflection → reflection
9. 以上都不命中                                    → ReadyForDecision → decision
```

第 3 步的 RISK-003 检查与第 7 步的 REFUND-003 检查，分别与 RISK-002、REFUND-008
的检查保持对称：证据/风险信号的可信度判断需要对同类场景一致处理，不能因为
"质量问题"和"商品损坏"、"新账号大额退款"和"大额退款"属于不同规则编号就区别
对待。具体设计记录见 `docs/10_ResultsReport.md`。

## 4.7 Decision Engine（`decision/engine.py` + `decision/rule_engine.py`）

`evaluate_decision(context)` 按 `priority` 从高到低遍历 `data/decision_rules.json`
中的规则，首个 `when` 条件全部满足的规则即命中，计算 `decision`/`approved_quantity`/
`approved_amount`/`decision_confidence`，规则支持的表达式：`path`（取值）、`min`
（取多个字段最小值）、`unit_price_times_quantity`（按订单项快照单价计算金额）、
`partial_if_less`（批准数量小于申请数量时改判 `PartialApprove`）。

配置文件加载时校验规则编号、条件、支持的操作符；无效配置直接抛
`DecisionRulesError`，不静默跳过。`automatic_policy_ids()` 从规则的 `when` 条件里
反推"这些政策编号命中时可以自动裁决"的集合，供 Evidence Gate 第 8 步使用。

## 4.8 Reflection Agent（`agents/reflection_agent.py`）

只在 `gate_result.status == "NeedsReflection"` 时被调用。按命中的政策编号给出
`route`（`request_more_evidence` / `await_logistics_review` / `human_escalation`），
不产生新事实，只决定"接下来该做什么"。

## 4.9 Finalization（`graph/finalization.py`）

系统里**唯一**允许写回 `service_requests.workflow_status`/`decision` 的节点
（见文件内注释）。三条输入路径：

```text
proposed_decision 非空        → 调用 persist_decision_lifecycle：
                                 同一事务写入 decision_records（不可变裁决快照）+
                                 action_tasks（幂等履约任务）+ outbox_events
reflection_result 非空        → 按 route 映射 workflow_status/handling_status
仅有 gate_result（无上两者）  → 按 gate_status 映射（HumanEscalation/NeedsMoreEvidence/
                                 AwaitingEvidenceReview）
```

`AwaitingEvidenceReview` 和 `NeedsMoreEvidence` 在持久化的 `workflow_status` 上都
是 `NeedsReflection`——这两种状态在写回时坍缊成了同一个值，只有当轮的 `handling_status`
能区分，之后再查询就分不清"还没传证据"和"传了在等审核"。当前的应对方式是在
读取时从证据列表反推（前端 `App.tsx` 的 `hasEvidenceAwaitingReview` 判断），
而不是改 `workflow_status` 的取值本身——后者会牵动 Staff 工作台的队列过滤和
人工裁决权限判断，改动面明显更大，具体取舍记录在 `docs/10_ResultsReport.md`。

## 4.10 Response Agent（`agents/response_agent.py`）

先用 `_build_template_response` 生成确定性模板文本（保证正确），可选再用 LLM
润色语气（`_polish_response`）。润色结果必须通过 `_contains_required_facts` 校验——
金额、数量、状态等关键事实必须原样出现在润色后的文本里，否则丢弃润色结果、
回退模板。系统提示词明确要求"避免逐项罗列的机器人报告体，改写成连贯自然的话"
（这条约束是发现语气评分偏低后加入的，见 `docs/10_ResultsReport.md`）。

Query Agent（`agents/query_agent.py`）的订单/物流查询走独立的 Tool-calling 路径
（`QUERY_TOOLS` + `_build_tool_executor`），不经过 Decision Engine，系统提示词同样
要求"必须包含关键事实，但不要逐项罗列"。

---

# 5. Action 节点设计要点

## 5.1 幂等与租约

`fulfillment_action_node` / `logistics_chase_action_node` 在数据库唯一索引层面
保证同一订单不重复创建"催发货"/"催物流"事件；已存在 `Submitted`/`InProgress`
记录时直接返回原记录，不是应用层判断后再插入（避免竞态）。

## 5.2 履约任务与 Outbox

`service_request_action_node` 走到自动裁决后，`finalization_node` 在同一事务里
创建 `action_tasks`（`idempotency_key` 唯一）和 `outbox_events`。真正的外部调用
（退款、取消订单、换货出库）由独立 Worker（`scripts/workers/run_action_worker.py`）
异步认领执行，使用 Postgres `SKIP LOCKED` 支持多实例并行消费，成功后
`ExecutionPending → Closed`，失败重试耗尽后进入 `ExecutionFailed`。

**该 Worker 默认不随 `docker compose up` 启动**——`docker-compose.yml` 给它设置了
`profiles: ["execution"]`，必须用 `docker compose --profile execution up` 才会带上；
如果只用不带 profile 的默认命令，所有"已批准"的申请会一直卡在 `ExecutionPending`，
表面上看起来批准成功，实际上退款/取消动作从未真正执行，详见 `docs/10_ResultsReport.md`。

Worker 通过环境变量对接外部履约服务：

```bash
PAYMENT_REFUND_URL=http://payment-service/refunds
WAREHOUSE_EXCHANGE_URL=http://warehouse-service/exchanges
ORDER_CANCEL_URL=http://order-service/cancellations
```

每次调用带 `Idempotency-Key`，按指数退避重试；每次任务创建、重试、成功或失败
都在同一事务中写入 Outbox 事件，不会因为重试而产生重复的履约动作。

## 5.3 待确认 Offer 机制

`conversation_router_agent.py` 里的 `_PENDING_OFFERS` 是一个通用的"提出建议 → 等
客户确认 → 才真正执行"模式，目前有三个实例：

```text
OfferExpediteShipment  → 确认后 intent=ExpediteShipmentConfirmation → fulfillment_action
OfferLogisticsChase    → 确认后 intent=LogisticsChaseConfirmation   → logistics_chase_action
OfferHumanHandoff      → 确认后 intent=HumanHandoff                 → human_handoff_action
```

`OfferHumanHandoff` 是三者中最后加入的：命中 30 天提交上限时，之前的实现会
不经确认直接转人工；现在改为先问"需要帮您转人工吗"，客户确认后才真正创建
转接记录，且转接原因从挂起时的 `conversation_context.pending_human_handoff_reason`
里取，而不是把客户确认时说的"需要"两个字当成转人工原因写入审计记录。

## 5.4 Staff 工作台闭环

`api/staff.py` + `api/staff_auth.py`：`/staff/*` 不信任前端提交的处理人姓名，
而是用 `.env` 中配置的不透明 Token 映射到服务端身份：

```bash
STAFF_API_KEYS='{"replace-with-long-random-token":{"staff_id":"agent_001","roles":["reviewer"]}}'
```

支持 `viewer`/`reviewer`/`admin` 三种角色。人工裁决前必须先领取工单（15 分钟
租约 + `row_version` 乐观锁，防止两名客服同时覆盖结果），批准金额必须
`<= 订单项实付金额`（`decision_lifecycle_repository.validate_human_decision`）——
这条上限校验与自动裁决路径（始终按 `unit_price × quantity` 计算）保持一致。
领取、证据审核、人工裁决和手动重试均记录在 `audit_logs`；达到最大重试次数的
履约任务进入工作台的"履约失败任务"队列，可由有权限的客服重新入队。

---

# 6. API 参考与本地开发

## 6.1 本地开发（不使用 Docker）

```bash
python -m pip install -r requirements.txt
uvicorn src.backend.api.main:app --reload
```

未设置 `DATABASE_URL` 时，系统回退到内存会话和 JSON 履约记录，仅适合本地
调试；需要持久化时按 `.env.example` 配置数据库连接。前端单独开发：

```bash
cd src/frontend
npm install
npm run dev
```

Vite 开发服务器将 `/api` 请求代理到 `http://127.0.0.1:8000`。

## 6.2 接口地址

- Health Check：`http://localhost:8000/health`
- Mock Customers：`http://localhost:8000/customers`
- Customer Orders：`http://localhost:8000/customers/{customer_id}/orders`
- Order & Logistics Detail：`http://localhost:8000/customers/{customer_id}/orders/{order_id}`
- Evidence Upload：`POST http://localhost:8000/service-requests/{request_id}/evidence`
- Evidence List：`GET http://localhost:8000/service-requests/{request_id}/evidence?customer_id={customer_id}`
- Chat API：`http://localhost:8000/chat`
- Swagger UI：`http://localhost:8000/docs`

多轮请求必须复用同一个 `conversation_id`：

```bash
curl -X POST http://localhost:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{
    "conversation_id": "customer-001",
    "customer_id": "CUS0021",
    "order_id": "ORD2026080021",
    "message": "订单ORD2026080021什么时候发货？"
  }'
```

`customer_id` 是会话身份边界；`order_id` 仅在从订单页进入客服时传入，且必须
属于当前客户。前端不传入意图，意图始终由 Agent 根据自然语言识别。

退款、部分退款和换货诉求会先创建 PostgreSQL `service_requests` 记录，新申请
初始状态为 `Pending`；质量问题的文字陈述不会被直接视为已验证证据，也不会
自动批准，需要进入证据提交与审核流程。

## 6.3 证据上传约束

支持 JPG、PNG 和 WebP，单文件最大 10MB。后端校验文件内容签名而非只信任扩展名
或浏览器 MIME；文件保存在 `data/uploads/`，PostgreSQL 只保存存储路径、类型、
大小和审核状态等元数据。新上传证据初始状态为 `Pending`，不代表商品问题已确认。
