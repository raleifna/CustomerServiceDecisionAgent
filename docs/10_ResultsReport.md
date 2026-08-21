# Results Report
# CustomerServiceDecisionAgent

> 本文档回答两个问题：系统现在处于什么状态（对应 `docs/01_PRD.md` 2.1 节
> Success Metrics 与 10 章 Acceptance Criteria），以及这个状态是怎么来的——
> 开发过程中真实发现并修复了哪些业务逻辑问题，而不是设计阶段就预见到的。
> 技术方案见 `docs/04_Architecture.md`/`06_LowLevelDesign.md`，评测方法论见
> `docs/07_Evaluation.md`；本文档只呈现结果和过程，不重复方法论细节。

---

# 1. 系统现状概览

| 维度 | 结果 |
|---|---|
| 确定性回归套件（`run_evaluation.py`） | **34 / 34** 通过（Routing、Decision、Context Memory、Thread Isolation、Intake、Action Success/Guard/Safety、Idempotency） |
| 单元测试（`python -m pytest tests -q -k "not postgres and not integration"`） | **41 / 41** 通过 |
| PostgreSQL 集成测试（`docker compose --profile test up integration-tests`） | **5 / 5** 通过（幂等、并发领取、事务回滚等真实数据库行为） |
| LLM-as-Judge 质量评测（`run_llm_judge.py`，7 条用例，4 条 `pass_k=3`） | **7 / 7** 通过，全部 `pass_k` 用例三次一致通过 |
| 端到端履约执行验证 | 已验证：批准 → `action_tasks` 创建 → Worker 认领 → 外部调用模拟 → `Succeeded`，全链路无人工介入 |

确定性套件和单元测试均已接入可重复运行的脚本，任何后续代码改动都应重新运行
这两项验证再下结论。

## 1.1 对照 PRD Success Metrics（`docs/01_PRD.md` 2.1 节）

| 指标 | 目标 | 当前证据 | 结论 |
|---|---|---|---|
| Decision Accuracy | > 90% | 确定性套件 Decision 类别 21/21（100%） | 达标，但样本是自建 Ground Truth，见第 4 节局限性 |
| Human Escalation Rate | < 15% | 未做全量统计；21 条决策用例中命中强制人工/复核路径的比例约为 6/21（29%），但这是刻意构造的边界覆盖矩阵，不是真实流量分布 | **不能下结论**，需要真实或更大规模随机流量才能算出有意义的比例，见第 4 节 |
| Average Response Time | < 5s | 未做系统性计时测量 | **未验证**，见第 4 节 |
| Workflow Success Rate | > 95% | 确定性套件 34/34（100%）+ 单元测试 41/41 + 集成测试 5/5 全部无异常终止 | 达标，同样受限于测试场景是自建而非真实流量采样 |

**Decision Accuracy 和 Workflow Success Rate 在当前自建测试集上达标**；
Human Escalation Rate 和 Average Response Time 目前缺乏真实测量，不作达标
判断，详见第 4 章。

---

# 2. 系统迭代中发现并修复的业务逻辑问题

以下问题均在系统联调完成、可以端到端运行之后，通过代码审查、真实交互测试和
评测过程发现，不是设计阶段的预判。每一条都对应一个具体的"输入 A 会导致错误
结果 B"的场景，用于说明"流程能跑通"和"业务逻辑正确"是两个需要分别验证的
维度。

## 2.1 REFUND-003 证据门槛缺失（决策引擎）

**问题**：REFUND-008（商品损坏）在 `evidence_gate.py` 里有一条检查——如果证据
只是机审通过（`MachineReviewed`）、没有人工确认（`Verified`），必须转人工复核，
不能自动放款。但 REFUND-003（质量问题）走的是同一套 `has_usable_evidence` 判定，
却没有对应拦截。

**触发场景**：客户以"质量问题"为由申请退款，仅上传图片被机审标记为
`MachineReviewed`（从未人工核实），系统直接命中 `quality_evidence` 规则自动批准
退款，完全跳过人工复核。同样的证据状态，如果理由填"商品损坏"就会被正确拦截。

**修复**：`evidence_gate.py` 给 REFUND-003 补上与 REFUND-008 对称的检查（见
`docs/06_LowLevelDesign.md` 4.6/4.9 节）。修复后两条相关的评测用例
（`EVAL_DECISION_005`/`008`）从"自动批准"变成"需要更多证据"——不是放宽断言
迁就旧行为，而是把种子证据状态从 `MachineReviewed` 改成 `Verified`，让用例
继续测原本想测的"证据齐全应自动批准"路径。

## 2.2 人工裁决金额没有上限校验（Staff 工作台）

**问题**：自动裁决路径的金额永远由 `unit_price × approved_quantity` 计算得出，
不可能超过订单实付；人工裁决路径（`validate_human_decision`）只校验"金额是正数"，
不校验是否超过该订单项的实付金额。

**触发场景**：`POST /staff/service-requests/{id}/resolve` 传
`approved_amount=999999`，只要客服已领取该工单、版本号匹配，系统照单全收。

**修复**：`validate_human_decision` 新增 `max_approved_amount` 参数，超出订单项
`paid_amount` 直接拒绝；调用方 `resolve_human_decision` 在校验前查出该订单项的
实付金额传入。补了两条单元测试（超额拒绝 + 恰好等于实付金额允许通过）。

## 2.3 RISK-003 隐性依赖 RISK-002（风险规则）

**问题**：RISK-003（新账号大额退款）的触发金额阈值与 RISK-002（大额退款）完全
相同，导致 RISK-003 出现时必然伴随 RISK-002，被后者的强制人工规则间接兜底。
`evidence_gate.py` 里没有对 RISK-003 的独立检查。

**风险**：这不是当前会触发的 bug（因为两个阈值目前相同），而是一处脆弱的隐性
耦合——一旦以后给新账号单独配一个更低的金额红线，RISK-003 会在没有任何拦截
的情况下静默通过。

**修复**：`evidence_gate.py` 给 RISK-003 加上与 RISK-002 同级的强制人工检查，
不再依赖阈值巧合。补了对应单元测试。

## 2.4 action-worker 默认不启动，履约任务永久卡在 Pending

**问题**：`docker-compose.yml` 给 `action-worker` 服务设置了
`profiles: ["execution"]`，默认的 `docker compose up` 不会启动它，也不会重启
一个已存在但已退出（比如因为网络重建而崩溃）的旧容器。

**影响**：这不是代码 bug，是一个会导致"看起来批准成功、实际上退款/取消/
换货从未真正执行"的运维陷阱——申请会一直停在 `ExecutionPending`。曾经出现过
worker 容器因网络重建导致 `failed to resolve host 'postgres'` 而崩溃退出，
且因为 profile 的原因，不带该参数的 `docker compose up -d` 无法重新拉起它。

**修复**：正确启动方式为 `docker compose --profile execution up -d`；已用
测试验证：申请批准后 4 秒内 worker 自动认领并执行了 `CancelOrder` 动作，
状态变为 `Succeeded` 并带有真实交易号。README"启动与体验"章节和
`docs/06_LowLevelDesign.md` 5.2 节均说明了这一启动要求。

## 2.5 LLM-as-Judge 评测本身的方法论漏洞

**问题**：`run_llm_judge.py` 最初只让 LLM 评委读最后一条 Agent 回复打分。在没有
配置 `DATABASE_URL` 的环境下运行时，`JUDGE_REASON_PHRASING_001` 用例触发的
`CreateServiceRequest` 动作因为拿不到数据库连接而失败，Agent 用一句得体的
"售后申请暂时未能创建，请稍后重试"兜底——评委只看文本，给了 3/5 的及格分，
把一条完全没跑通的执行链路误判为"通过"。

**修复**：脚本现在会捕获每轮对话真实的 `action_result`/`final_result`，任何
`action_result.status == "Failed"` 或"预期应触发动作但没有触发"的用例，
**不采纳评委的文本分数，直接强制判定为失败**。同时新增了 `policy_compliance`
维度（回复陈述的结果是否与系统真实裁决一致）和 `pass_k` 多次运行一致性检验
（详见 `docs/07_Evaluation.md` 7.3/7.4 节）。

另需说明一类评委自身的判断误差：加入 `policy_compliance` 维度后，`pass_k=3`
的 `JUDGE_REASON_PHRASING_001` 曾有一次被评委打了 1 分，理由是"系统事实显示
申请未创建成功"。核对数据库记录后确认申请实际创建成功（`SR2026081001`，
`Approve`，359 元，与订单实付一致），评委把正常的幂等去重返回值
（`action_result.status == "Existing"`，表示命中已存在的同订单同类型同原因
申请、直接复用而非报错）误读为失败。这是 LLM 评委自身的判断误差，不是 Agent
的缺陷，处理方式是在评委 Prompt 中明确解释 `Created`/`Existing`/`Failed`
三种状态各自的含义。这类误差说明"确定性检查 + LLM 评委 + 人工复核异常结果"
三层缺一不可——只用 LLM 评委单层打分，无法区分"系统真的错了"和"评委读错了
系统状态"。

## 2.6 前端：证据卡片会被后续对话覆盖 / 漂移到底部

**问题**（用户实际测试中发现，非代码审查发现）：客户提交证据后，卡片状态
（"证据已提交"）绑定在单一全局状态上，被驳回后重新上传会直接覆盖/重置这个
状态，历史提交记录无法保留；同时卡片渲染在消息列表之外，每次新消息都会把它
"挤"到聊天最下面。

**修复**：把单一的 `uploadedEvidence`/`evidenceCardMessageId` 状态改成按锚点
消息 id 索引的 `evidenceCards` 映射——每次创建申请或收到驳回通知都开一张新卡片，
锚定在触发它的那条消息下面，历史卡片永久保留在原位。同时给"我的售后"页面加了
证据画廊（缩略图 + 状态 + 描述），并且修了一个关联的状态坍缩问题：
`AwaitingEvidenceReview`（已提交等审核）和 `NeedsMoreEvidence`（需要新材料）在
持久化的 `workflow_status` 上是同一个值，前端原本没法区分，改成从证据列表本身
反推是否已有材料在等审核，避免邀请客户做多余的重复上传。

---

# 3. 评测覆盖的取舍记录

- **evaluation 用例修复不是放宽断言**：REFUND-003 修复后失败的两条用例，处理
  方式是让种子数据（证据状态）符合"这条用例本来想测什么"，而不是改断言迁就
  代码的新行为——如果反过来做，回归套件会失去对同类 bug 的保护能力。
- **AwaitingEvidenceReview 状态坍缩没有改数据库 schema**：技术上可以给
  `workflow_status` 新增一个持久化值，但这会牵动 Staff 工作台的队列过滤逻辑
  和人工裁决权限判断（`resolve_human_decision` 目前允许对 `NeedsReflection`
  状态的工单裁决，如果拆分状态还要重新决定 `AwaitingEvidenceReview` 是否也该
  允许裁决，这是需要业务方确认的政策问题，不是单纯的技术改动）。因此选择了
  风险更小的方案：在已经读取的证据列表上做读时推断。
- **RISK-003 选择显式加检查，而不是只写注释说明依赖关系**：因为隐性依赖一旦
  被后续调参打破，不会有任何报错或测试失败提示，是最容易被忽略的一类 bug。
- **回归测试覆盖**：第 2 章修复的三个业务逻辑问题（REFUND-003、金额上限、
  RISK-003）均已补充对应单元测试，不再只依赖评测脚本间接覆盖。

---

# 4. 已知局限性

- **Human Escalation Rate 和 Average Response Time 两项 Success Metrics 未验证**：
  当前测试集是刻意构造的业务场景覆盖矩阵（`docs/02_DataDesign.md` 2.3 节），
  不是真实流量的随机采样，据此计算的"人工升级占比"不具备统计意义；响应时长
  也未做过系统性计时。这两项指标应标注为"未实测"，而非默认已达标。
- **LLM-as-Judge 评测样本量较小**（7 条用例）：足以验证评测方法论本身是否
  可靠（见第 2.5 节），但样本量不足以支撑"语气质量已充分验证"的结论，扩大
  场景覆盖是后续工作方向。
- **AwaitingEvidenceReview 状态坍缩仅在前端读取侧解决**：后端 `workflow_status`
  仍将两种状态存为同一个值；若未来新增需要区分这两种状态的消费方（如新的
  报表或 Staff 端功能），需重新评估是否值得做持久化层面的拆分。
- **未做安全专项审查**：本阶段工作聚焦业务逻辑正确性和评测方法论，尚未进行
  完整的安全渗透测试或依赖漏洞扫描。
