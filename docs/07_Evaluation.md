# Evaluation

## 1. 评估目标

本项目使用轻量 Evaluation Harness 对 Agent 的确定性业务行为做回归验证。Harness 不是训练系统，也不使用真实电商对话作为标签；它读取人工设计的 Ground Truth Case，运行 LangGraph，并比较结构化结果。

当前覆盖：

- Routing Accuracy：查询、物流、售后与 Action 是否进入正确分支；
- Decision Accuracy：21 条 Service Request 的处理状态、结论、数量和金额；
- Context Accuracy：多轮对话能否继承当前订单与申请；
- Thread Isolation：不同 `thread_id` 是否互相隔离；
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

当前基线共 33 条：

| 类别 | 通过数 / 总数 |
|---|---:|
| Routing | 5 / 5 |
| Decision | 21 / 21 |
| Context Memory | 2 / 2 |
| Thread Isolation | 1 / 1 |
| Action Success | 1 / 1 |
| Idempotency | 1 / 1 |
| Action Safety | 1 / 1 |
| Action Guard | 1 / 1 |
| **合计** | **33 / 33** |

该结果只表示当前 Mock DB 与 Ground Truth 覆盖的 33 条业务路径全部通过，不应解释为系统在真实客户消息上的准确率为 100%。后续每发现一种失败模式，应先增加可复现 Case，再修复代码。
