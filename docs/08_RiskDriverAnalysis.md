# Risk Driver Analysis
# 关键驱动与风险归因分析

> 本文档回答两个问题：哪些变量真正驱动了裁决结果的差异（而不是表面相关），
> 以及在这些驱动因子里，哪些属于高影响、高不确定性、值得优先复核的场景。
> 结论直接来自 `data/decision_rules.json`、`data/business_rules.json` 和
> `src/backend/agents/policy_agent.py` 的真实实现，不是抽象推演。

---

# 1. 结果差异的驱动因子拆分

一条 Service Request 最终走向 Approve / Reject / PartialApprove / NeedsReflection /
EscalatedToHuman，由以下几类变量共同决定，按驱动力从强到弱排列：

## 1.1 硬性事实变量（一票通过或否决，不受置信度调节）

| 变量 | 来源 | 驱动的规则 | 影响 |
|---|---|---|---|
| `is_shipped`（是否已发货） | Orders.shipped_date | `cancel_unshipped`（priority 100，最高） | 未发货可直接批准取消，不需要走证据审核，这是唯一不依赖任何证据的自动批准路径 |
| `product_match` / `confirmed_missing_quantity` | 订单 vs 仓库出库记录 | `wrong_product`、`missing_product` | 仓库记录可独立验证客户主张，不依赖客户材料，priority 仅次于未发货取消 |
| `is_customized`（是否定制） | Products.is_customized | `customized_no_reason` | 定制商品一律拒绝无理由退货，硬性例外，不进入置信度判断 |
| `days_since_delivery` vs 时效阈值 | Orders.delivered_date | `late_no_reason`、`no_reason_return` | 时效是否超期是二元判断，没有"接近超期打折扣"这种中间态 |

这类变量的共同特点：规则命中后**没有置信度概念**，因为事实本身就是确定的，
不存在"70% 确信仓库记录准确"这种中间状态——这是本项目区别于"一切都算置信度"
设计的关键取舍，详见 `docs/09_StrategyRollout.md` 第 3 章的方案比较。

## 1.2 证据状态变量（决定能不能自动裁决，而非裁决方向）

| 变量 | 取值 | 驱动效果 |
|---|---|---|
| `evidence_review.has_usable_evidence` | `Verified` 或 `MachineReviewed` 即为真 | 决定 `quality_evidence`/`damage_with_verified_evidence` 规则能否触发 |
| `evidence_review.has_machine_reviewed_evidence` 且 `!has_verified_evidence` | — | 即使 `has_usable_evidence=true`，仍会被 Evidence Gate 拦截转 Reflection（REFUND-003/REFUND-008，见 §3.1） |
| `evidence_review.has_pending_evidence` | — | 直接进入 `AwaitingEvidenceReview`，不解释为"证据不足" |

**这一层是本项目实际迭代中风险归因暴露问题最集中的一层**——REFUND-003 的证据
门槛缺失（详见 `docs/10_ResultsReport.md` 2.1 节）就是这一层驱动因子建模不完整
导致的：`has_usable_evidence` 这个二元变量掩盖了"机审"和"人工核实"两种可信度
完全不同的子状态，直到专门为 REFUND-003 补上区分后才纠正。

## 1.3 风险信号变量（可以叠加，触发强制人工，不受其他因素豁免）

| 信号 | 阈值来源（`business_rules.json`） | 驱动逻辑 |
|---|---|---|
| RISK-001 高频退款 | `risk.high_refund_count = 10` | 客户历史成功退款次数 >= 10 |
| RISK-002 大额退款 | `risk.high_value_amount = 5000.0` | 订单金额 >= 5000 元 |
| RISK-003 新账号大额退款 | `risk.new_account_days = 30` + 同上金额阈值 | 注册 <= 30 天 + 首次退款 + 金额 >= 5000 元 |
| RISK-004 主张冲突 | — | 客户"错发"主张与仓库出库记录矛盾，或申请数量超过订单/剩余可赔付数量 |
| RISK-005 决策边界声明 | — | 命中 RISK-004/RISK-006 或证据要求人工审核时一并追加，声明 `NeedsReflection`/`EscalatedToHuman` 是流程状态而非最终结论 |
| RISK-006 少发数量超额 | — | 客户申请数量超过仓库记录确认的少发数量，只能对已确认数量 `PartialApprove`，不能按申请数量全额批准 |

**关键发现**：RISK-003 的金额阈值目前与 RISK-002 完全相同，这不是巧合规则设计，
而是当前参数配置下的一个真实耦合——见第 2 章敏感性分析。

---

# 2. 参数敏感性分析

`business_rules.json` 里的四个风险阈值直接决定 Human Escalation Rate（见
`docs/01_PRD.md` 2.1 节 Success Metrics），调整任何一个都会改变裁决结果分布，
而不需要改一行 Python 代码——这是声明式规则设计的直接收益，但也意味着阈值本身
需要被当作需要持续复核的风险面，而不是配置一次就不再关注。

| 阈值 | 当前值 | 调高的影响 | 调低的影响 |
|---|---|---|---|
| `high_refund_count` | 10 | 更多高频退货客户绕过 RISK-001 复核，恶意退货识别率下降 | 更多正常复购客户被误判为高风险，人工负担上升、客户体验下降 |
| `high_value_amount` | 5000.0 | 更大金额的订单可以自动裁决，误放款的单笔损失敞口扩大 | 更多正常订单被迫转人工，Human Escalation Rate 上升，自动化收益下降 |
| `new_account_days` | 30 | 更短的"新账号"认定窗口，遗漏更多真实的新账号欺诈窗口期 | 更多老账号被误判为"新账号"，误伤正常客户 |
| `max_after_sales_submissions_per_order_30_days` | 3 | 恶意反复提交更容易绕过上限 | 正常客户因同一订单多次合理申请被误挡（不含纯咨询，计数只统计 `service_requests` 创建次数） |

**高不确定性点**：`high_refund_count` 和 `high_value_amount` 这两个数字目前是
沿用初始配置的固定值，没有基于真实退款分布做过统计校准（例如"多少百分位的订单
金额落在 5000 元以上"）。在没有真实流量数据之前，这两个阈值的合理性无法验证，
这是 `docs/10_ResultsReport.md` 第 4 章"已知局限性"里 Human Escalation Rate
无法给出可信数字的根本原因——阈值本身就是未经真实数据校准的假设。

---

# 3. 已处理的高影响风险点

以下两处曾经是风险归因链条上的实际缺口，均已在 Evidence Gate / Policy Agent
层面收敛：

## 3.1 RISK-003 与 RISK-002 阈值耦合

如 §1.3 所述，RISK-003 的触发条件是 RISK-002 触发条件的子集（新账号是额外
必要条件，但金额阈值完全相同）。任何触发 RISK-003 的请求必然同时触发
RISK-002，被后者的强制人工规则兜底；但这是参数巧合，不是显式防护，
`evidence_gate.py` 已加入独立于 RISK-002 的 RISK-003 检查，不再依赖这个巧合。
若未来业务方要求给"新账号"单独设一个比 5000 元更低的金额红线（新账号的
风险容忍度理应比老账号更低，是欺诈防控中常见诉求），需要重新验证 §1.3
表格里两条规则的触发顺序是否仍然符合预期。

## 3.2 证据可信度层级建模

`evidence_review.has_usable_evidence` 这一个布尔值曾经把"机器初审"和"人工
核实"两种置信度差异巨大的证据状态合并成同一个"可用"标签，导致质量问题类
退款可以仅凭机审证据自动放款（REFUND-003）。Evidence Gate 现在对
`has_machine_reviewed_evidence` 且未 `has_verified_evidence` 的情况单独拦截，
强制转入 Reflection（见 §1.2）。这类"变量粒度不够细，掩盖了关键差异"的问题，
是风险归因中最值得警惕的一类——不是规则写错了，而是变量本身没有区分该区分
的东西。
