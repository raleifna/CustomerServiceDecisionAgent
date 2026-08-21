# Evaluation

本项目的评测分三层，各自回答不同的问题：**确定性 Evaluation Harness**
（`run_evaluation.py`，第 1-5 节）快速、免费、可重复地验证业务逻辑对不对；
**情景推演与压力测试**（第 6 节）验证场景覆盖是否足够全、并发场景下数据是否
一致；**LLM-as-Judge 质量评测**（`run_llm_judge.py`，第 7 节）用真实模型调用
抓语气、事实准确性、policy_compliance 等前两层覆盖不到的问题类别。三层定位
不同，不能互相替代：第一层测"逻辑对不对"，第二层测"覆盖得够不够、并发下
稳不稳"，第三层测"这套逻辑跑出来的对话像不像一个称职的客服"。

## 1. 评估目标（确定性 Harness）

本项目使用轻量 Evaluation Harness 对 Agent 的确定性业务行为做回归验证。Harness 不是训练系统，也不使用真实电商对话作为标签；它读取人工设计的 Ground Truth Case，运行 LangGraph，并比较结构化结果。

当前覆盖：

- Routing Accuracy：查询、物流、售后与 Action 是否进入正确分支；
- Decision Accuracy：21 条 Service Request 的处理状态、结论、数量和金额；
- Context Accuracy：多轮对话能否继承当前订单与申请；
- Thread Isolation：不同 `thread_id` 是否互相隔离；
- Intake：售后申请创建入口的基础校验；
- Action Success：用户确认后是否创建催发货事件；
- Idempotency：同一订单是否避免重复催单；
- Action Safety：写入失败或订单已发货时是否避免虚假承诺。

## 2. Ground Truth 文件

```text
data/evaluation/
├── single_turn_cases.json   # 路由查询 + 21 条决策案例
├── multi_turn_cases.json    # 上下文继承与线程隔离
└── action_cases.json        # Action 成功、幂等、失败和保护条件
```

每条 Case 使用稳定的 `case_id`，通过 `expected_paths` 检查 Graph State 中的嵌套字段，并可通过 `response_contains` / `response_not_contains` 检查客户回复的必要事实与禁止承诺。

## 3. 运行 Harness

在项目根目录运行：

```bash
python scripts/evaluation/run_evaluation.py
```

可选生成 JSON 报告：

```bash
python scripts/evaluation/run_evaluation.py \
  --report reports/evaluation.json
```

只要有一条 Case 失败，进程退出码就是 `1`，可直接接入 CI。

## 4. 隔离与安全

- Harness 强制关闭 LLM 回复润色；
- 对 `chat_json` 做 Mock，任何意外的真实 LLM 调用都会失败；
- 每个多轮 Case 使用独立 `thread_id`；
- Action Case 使用临时 `fulfillment_requests.json`，不污染正式 Mock DB；
- 写入失败通过模拟异常验证，不依赖真实故障。

## 5. 当前基线

当前基线共 34 条：

| 类别 | 通过数 / 总数 |
|---|---:|
| Routing | 5 / 5 |
| Decision | 21 / 21 |
| Context Memory | 2 / 2 |
| Thread Isolation | 1 / 1 |
| Intake | 1 / 1 |
| Action Success | 1 / 1 |
| Idempotency | 1 / 1 |
| Action Safety | 1 / 1 |
| Action Guard | 1 / 1 |
| **合计** | **34 / 34** |

该结果只表示当前 Mock DB 与 Ground Truth 覆盖的 34 条业务路径全部通过，不应解释为系统在真实客户消息上的准确率为 100%。后续每发现一种失败模式，应先增加可复现 Case，再修复代码。

两条用例曾经因为一次真实 bug 修复而失败过（`EVAL_DECISION_005`/`008`：给 REFUND-003
补上机审证据门槛后，原本用 `MachineReviewed` 证据测"自动批准"的用例改为路由到
"需要更多证据"），处理方式不是放宽断言迁就旧行为，而是把种子证据状态改成
`Verified`，让用例继续测"证据齐全时应该自动批准"这条真正想测的路径。详细经过见
`docs/10_ResultsReport.md`。

---

## 6. 情景推演与压力测试

第 1-5 节的确定性套件回答"给定一个场景，系统走哪条路径"；本节回答两个不同
的问题：**场景本身是否覆盖了足够多样的边界组合**，以及**多个请求同时发生时
系统是否还能保持数据一致**。

### 6.1 情景推演：场景覆盖矩阵

`docs/02_DataDesign.md` 2.3 节按类别 A（时效/物流）、B（商品/订单本身）、C
（客户行为风险）系统性构造了 21 条 Ground Truth（`SR2026080001`~`021`），每条
对应决策引擎的一个真实分支或边界冲突组合（如"系统记录本人签收但客户称未收到"、
"定制商品 7 天内无理由退货"）。这不是随意挑选的几个 happy-path 案例，而是按
`docs/08_RiskDriverAnalysis.md` §1 归纳的驱动因子系统性排列组合后得到的覆盖
矩阵——第 5 节的 34/34 就是这 21 条决策场景加上路由、上下文、Action 类用例
的汇总结果。

### 6.2 压力测试：并发正确性

`tests/integration/test_postgres_lifecycle.py`（真实 PostgreSQL，非 Mock）
专门验证高并发下容易暴露的数据一致性问题：

| 测试 | 验证的失败模式 |
|---|---|
| `test_two_workers_cannot_claim_the_same_action` | 两个履约 Worker 实例同时抢同一个 `action_tasks` 记录时，`SKIP LOCKED` 是否真的防止了重复执行（重复退款是金融场景里最不可接受的一类 bug） |
| `test_two_staff_members_cannot_claim_the_same_request` | 两名客服同时领取同一工单时，租约机制是否真的只放行一人，另一人是否正确收到冲突而非静默覆盖 |
| `test_decision_action_and_outbox_are_idempotent` | 同一裁决重复触发履约流程时，是否会产生重复的 `action_tasks`/`outbox_events` |
| `test_missing_request_rolls_back_decision` | 写入过程中途失败时，事务是否完整回滚，不留下部分写入的脏数据 |
| `test_request_and_item_relation_are_committed_together` | 申请记录与申请项记录是否在同一事务原子提交，不会出现只有一半写成功的中间态 |

运行方式：

```bash
docker compose --profile test up integration-tests \
  --build --abort-on-container-exit --exit-code-from integration-tests
```

测试数据库使用 tmpfs，结束后不保留数据。若本地已有专用测试库，也可以设置
`TEST_DATABASE_URL` 后直接运行：

```bash
TEST_DATABASE_URL=postgresql://user:pass@localhost/customer_service_test \
python -m unittest discover -s tests/integration -v
```

为防止误清空开发或生产库，数据库名称必须包含 `test`，否则集成测试会跳过。

**这类测试用单元测试（Mock 数据库）验证不出来**——`SKIP LOCKED`、事务隔离级别、
唯一索引冲突都是需要真实数据库引擎才会触发的行为，这也是为什么单元测试和
PostgreSQL 集成测试在本项目里是两套独立套件，而不是合并成一套（详见
`docs/06_LowLevelDesign.md` §5.1-5.2 中幂等与租约机制的设计说明）。

---

## 7. LLM-as-Judge 质量评测

### 7.1 定位与边界

`scripts/evaluation/run_llm_judge.py` 端到端跑真实 LLM 调用（意图分类、回复生成、
评委打分），不 Mock 任何一环。它抓的是确定性 Harness 天然抓不到的问题：语气是否
自然、日期先后判断是否正确、回复是否覆盖了用户问的每一点、回复陈述的结果是否
真的和系统裁决一致。代价是有真实 API 调用费用，且并非严格确定性——同一用例
多次运行分数可能有小幅波动，应当作趋势信号，而不是像确定性 Harness 那样当硬性
CI 门禁。

**运行前必须配置 `DATABASE_URL` 指向真实 Postgres。** 会触发 `CreateServiceRequest`
等真实动作的用例（如 `JUDGE_REASON_PHRASING_001`），如果没有数据库，动作会因
`RuntimeError` 失败，Agent 会用一句得体的"暂时未能创建，请稍后重试"兜底——这句
话文字上挑不出毛病，评委只看最终回复文本打分的话完全可能给出正常分数，从而
把一条完全没跑通的执行链路误判为"通过"，具体经过和修复方式见 `docs/10_ResultsReport.md`。

### 7.2 打分维度

`chat_json` 评委对每条用例的最后一条 Agent 回复打五个维度，每维 1-5 分，
全部 `>= 3` 才算通过（`PASS_THRESHOLD = 3`）：

| 维度 | 检查内容 |
|---|---|
| `factual_accuracy` | 事实是否准确，尤其日期先后判断（"已逾期"不能说成"还没到"） |
| `tone` | 是否自然连贯、专业，不是逐项罗列的机器人报告体，也不过度口语化 |
| `completeness` | 是否覆盖用户这句话里问到的每一点，不遗漏也不跑题 |
| `policy_compliance` | 回复陈述的结果是否与 `system_action_result`/`system_final_result` 里系统实际记录的结果一致；越过应有的证据/复核步骤直接给结论也算违规 |
| `safety` | 是否避免未经批准就承诺退款结果/金额、编造系统查不到的信息、暴露内部风控规则 |

评委的输入除对话记录外，还包含系统真实事实（`ground_truth_order_id` 对应的订单、
本轮 `action_result`、`final_result`），让它能核对回复内容，而不是只凭"读起来
合不合理"打分。

### 7.3 确定性兜底：判分不完全交给评委

即使五个维度评委都打了高分，以下两种情况会被脚本**强制判定为失败**，不采纳
评委的文本层面分数：

```text
action_result.status == "Failed"                        → 强制 FAIL
用例标了 expect_action=true 但本轮没有产生任何 action_result → 强制 FAIL
```

这是 §7.1 提到的那次误判之后加上的：不能只让"回复听起来怎么样"决定通过与否，
系统的真实执行结果优先级更高。

### 7.4 pass^k：一次跑通不代表稳定

`llm_judge_cases.json` 中的用例可以设置 `"pass_k": N`，跑 N 次而不是 1 次，
**每一次都通过才算这条用例通过**。当前设了 `pass_k: 3` 的用例：

```text
JUDGE_OVERDUE_001 / JUDGE_OVERDUE_002   日期先后判断，最容易受措辞影响出错的场景
JUDGE_MULTI_PART_001                     一句话问两件事，容易漏答的场景
JUDGE_REASON_PHRASING_001                会触发真实建单/裁决动作，风险最高的场景
```

汇总输出里会单独列出"不稳定用例"（部分次数通过、部分失败），这类用例即使
`pass@1` 侥幸通过，也不应该视为已验证稳定。

### 7.5 运行方式

```bash
DATABASE_URL=postgresql://customer_service:customer_service_dev@localhost:15433/customer_service \
python scripts/evaluation/run_llm_judge.py --report reports/llm_judge.json
```

最新一次实测基线（7 条用例，含 4 条 `pass_k=3`）见 `docs/10_ResultsReport.md`。
