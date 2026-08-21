# Evaluation

本项目有两套互补的评测：**确定性 Evaluation Harness**（`run_evaluation.py`，本章
1-5 节）用于快速、免费、可重复的业务逻辑回归；**LLM-as-Judge 质量评测**
（`run_llm_judge.py`，本章第 6 节）用真实模型调用抓语气、事实准确性、
policy_compliance 等确定性检查覆盖不到的问题类别。两者定位不同，不能互相替代：
前者测"逻辑对不对"，后者测"这套逻辑跑出来的对话像不像一个称职的客服"。

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
`docs/09_ResultsReport.md`。

---

## 6. LLM-as-Judge 质量评测

### 6.1 定位与边界

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
把一条完全没跑通的执行链路误判为"通过"。这个坑是真实踩过的，具体经过和修复方式
见 `docs/09_ResultsReport.md`。

### 6.2 打分维度

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

### 6.3 确定性兜底：判分不完全交给评委

即使五个维度评委都打了高分，以下两种情况会被脚本**强制判定为失败**，不采纳
评委的文本层面分数：

```text
action_result.status == "Failed"                        → 强制 FAIL
用例标了 expect_action=true 但本轮没有产生任何 action_result → 强制 FAIL
```

这是 §6.1 提到的那次误判之后加上的：不能只让"回复听起来怎么样"决定通过与否，
系统的真实执行结果优先级更高。

### 6.4 pass^k：一次跑通不代表稳定

`llm_judge_cases.json` 中的用例可以设置 `"pass_k": N`，跑 N 次而不是 1 次，
**每一次都通过才算这条用例通过**。当前设了 `pass_k: 3` 的用例：

```text
JUDGE_OVERDUE_001 / JUDGE_OVERDUE_002   日期先后判断，最容易受措辞影响出错的场景
JUDGE_MULTI_PART_001                     一句话问两件事，容易漏答的场景
JUDGE_REASON_PHRASING_001                会触发真实建单/裁决动作，风险最高的场景
```

汇总输出里会单独列出"不稳定用例"（部分次数通过、部分失败），这类用例即使
`pass@1` 侥幸通过，也不应该视为已验证稳定。

### 6.5 运行方式

```bash
DATABASE_URL=postgresql://customer_service:customer_service_dev@localhost:15433/customer_service \
python scripts/evaluation/run_llm_judge.py --report reports/llm_judge.json
```

最新一次实测基线（7 条用例，含 4 条 `pass_k=3`）见 `docs/09_ResultsReport.md`。
