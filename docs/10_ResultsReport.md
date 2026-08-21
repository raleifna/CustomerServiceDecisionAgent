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

以上数字截至最近一次运行（commit `16883db`），不代表"永久成立"——确定性套件
和单元测试已接入可重复运行的脚本，任何后续改动都应该重跑这两项再下结论。

## 1.1 对照 PRD Success Metrics（`docs/01_PRD.md` 2.1 节）

| 指标 | 目标 | 当前证据 | 结论 |
|---|---|---|---|
| Decision Accuracy | > 90% | 确定性套件 Decision 类别 21/21（100%） | 达标，但样本是自建 Ground Truth，见第 5 节局限性 |
| Human Escalation Rate | < 15% | 未做全量统计；21 条决策用例中命中强制人工/复核路径的比例约为 6/21（29%），但这是刻意构造的边界覆盖矩阵，不是真实流量分布 | **不能下结论**，需要真实或更大规模随机流量才能算出有意义的比例，见第 5 节 |
| Average Response Time | < 5s | 未做系统性计时测量 | **未验证**，见第 5 节 |
| Workflow Success Rate | > 95% | 确定性套件 34/34（100%）+ 单元测试 41/41 + 集成测试 5/5 全部无异常终止 | 达标，同样受限于测试场景是自建而非真实流量采样 |

诚实的结论是：**Decision Accuracy 和 Workflow Success Rate 在当前自建测试集上达标**，
但 Human Escalation Rate 和 Average Response Time 目前缺乏真实测量，不应该在报告
里含糊带过说"已达标"。

---

# 2. 本轮开发中发现并修复的业务逻辑问题

以下问题全部是在系统已经"看起来能跑"之后，通过代码审查、真实交互测试或评测
过程发现的，不是设计阶段的预判——这一点本身也是这份报告想说明的：**能跑通
demo 和逻辑正确是两回事**，下面每一条都对应一个具体的"输入 A 会导致错误结果 B"
的场景。

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

**影响**：这不是代码 bug，是一个真实会导致"看起来批准成功、实际上退款/取消/
换货从未真正执行"的运维陷阱——申请会一直停在 `ExecutionPending`。开发过程中
这个问题真实发生过：worker 容器因为 `failed to resolve host 'postgres'`（指向
一个已被重建的旧网络）崩溃退出，且因为 profile 的原因，后续多次
`docker compose up -d` 都没有把它带起来。

**修复**：清理失效容器，用 `docker compose --profile execution up -d` 正确
启动；补充测试验证：批准后 4 秒内 worker 自动认领并执行了 `CancelOrder`
动作，状态变为 `Succeeded` 并带有真实交易号。README"启动与体验"章节和
`docs/06_LowLevelDesign.md` 5.2 节都补充了这个提醒，避免下次演示时踩同一个坑。

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
（详见 `docs/07_Evaluation.md` 6.3/6.4 节）。

补充说明一个插曲：加了 `policy_compliance` 维度后，`pass_k=3` 的
`JUDGE_REASON_PHRASING_001` 有一次被评委打了 1 分，理由是"系统事实显示申请未
创建成功"——但核对数据库记录后确认申请其实建成功了（`SR2026081001`，
`Approve`，359 元，与订单实付分毫不差），评委把正常的幂等去重返回值
（`action_result.status == "Existing"`，即"命中了已存在的同订单同类型同原因
申请，直接复用而不是报错"）误读成了失败。这是 LLM 评委自身的判断失误，不是
Agent 的 bug——处理方式是在评委 Prompt 里明确解释 `Created`/`Existing`/`Failed`
三种状态各自的含义，而不是弱化 policy_compliance 这个维度本身。这个插曲也说明
了为什么"确定性检查 + LLM 评委 + 人工复核异常结果"要三层都有，缺一层都可能
把错误的结论当成结果。

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
  允许裁决——这是一个业务政策决定，不是本轮顺手能改的技术细节）。选择了风险
  更小的方案：在已经读取的证据列表上做读时推断。
- **RISK-003 选择显式加检查，而不是只写注释说明依赖关系**：因为隐性依赖一旦
  被后续调参打破，不会有任何报错或测试失败提示，是最容易被忽略的一类 bug。

---

# 4. 工程实践现状

- **Git 历史**：项目此前有大量（136 个文件）跨会话累积的未提交改动，已按模块
  拆分为 4 个提交（`206dfdd` 移除废弃前端脚手架、`3455195` 后端决策引擎与
  Staff 工作台、`aeac222` 前端、`c07025d` LLM-judge 评测与文档配置，加上本轮
  `16883db`）。这些提交边界是按目录/模块归类的，不代表真实开发时间线——原始
  提交历史无法找回，这一点在提交时已如实告知，不冒充"这就是真实开发过程"。
- **回归测试覆盖**：本轮修复的三个真实业务逻辑 bug（REFUND-003、金额上限、
  RISK-003）都补了对应的单元测试，不再只依赖评测脚本间接覆盖。
- **未纳入本仓库的内容**：`EchoMind所有代码+简历/` 目录（简历/作品集材料，其中
  一个子目录本身是独立 git 仓库）不属于本项目范围，未提交进版本库，留待用户
  自行决定如何处理。

---

# 5. 已知局限性（如实说明，不回避）

- **Human Escalation Rate 和 Average Response Time 两项 Success Metrics 未验证**：
  当前测试集是刻意构造的业务场景覆盖矩阵（`docs/02_DataDesign.md` 2.3 节），
  不是真实流量的随机采样，用它算出的"人工升级占比"没有统计意义；响应时长也
  从未做过系统性计时。如果要在报告或面试中引用这两项指标，应该明确说明"未
  实测"，而不是含糊带过。
- **LLM-as-Judge 评测样本量小**（7 条用例）：足够抓出方法论层面的问题（如
  第 2.5 节两个插曲），但样本量不足以支撑"语气质量已经充分验证"这样的结论，
  更大规模的场景覆盖是明确的后续工作。
- **AwaitingEvidenceReview 状态坍缩只在前端读取侧解决**：后端 `workflow_status`
  本身仍然把两种状态存成同一个值；如果未来有新的消费方（比如新的报表或
  Staff 端功能）需要区分这两种状态，会重新遇到同样的问题，需要重新评估是否
  值得做持久化层面的拆分。
- **未做安全专项审查**：本轮工作聚焦业务逻辑正确性和评测方法论，没有做过
  完整的安全渗透测试或依赖漏洞扫描。
