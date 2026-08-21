# Decision Report & Roadmap
# 决策汇报与落地路线图

> 本文档面向管理层视角：系统现在能不能用、证据是什么、下一步往哪走、以及
> 评审时最可能被追问的问题该怎么答。与 `docs/10_ResultsReport.md`（工程视角
> 的测试结果记录）不同，本文档把结论压缩成可执行判断，并补上路线图和追问
> 应答，服务于汇报和评审场景。

---

# 1. 管理层结论（结论先行）

1. **系统已经具备生产可用的决策闭环**：从客户提出诉求到自动裁决或转人工，
   全流程真实跑通并通过测试验证，不是只有 UI 的静态 Demo。证据：确定性回归
   套件 34/34、单元测试 41/41、PostgreSQL 并发正确性测试 5/5、LLM 质量评测
   7/7（4 条关键场景验证了三次运行一致），见第 2 章证据清单。
2. **系统内建的安全边界经过实际验证，不是纸面设计**：迭代过程中发现并修复了
   3 个可能导致误放款或权限失控的业务逻辑漏洞，全部在系统集成后才暴露，说明
   设计阶段设想的安全边界与代码实际执行的安全边界需要分别验证。证据：
   `docs/10_ResultsReport.md` 2.1-2.3 节。
3. **两项 Success Metrics 尚未验证，不建议在没有真实数据前对外承诺具体数字**：
   Human Escalation Rate 和 Average Response Time 目前只有自建测试场景下的
   观察值，不具备统计意义，详见第 2 章"尚未闭合的证据"。
4. **距离生产上线还差风险阈值校准和真实流量验证两步**：技术闭环已经具备，
   `docs/08_RiskDriverAnalysis.md` 第 2 章指出的阈值未经真实数据校准，是
   当前最大的、且只能靠接入真实数据才能解决的不确定性。

---

# 2. 关键证据清单

| 结论 | 证据 | 出处 |
|---|---|---|
| 决策逻辑正确 | 确定性回归套件 34/34（Routing/Decision/Context/Thread Isolation/Intake/Action×3） | `docs/07_Evaluation.md` §5 |
| 并发与数据一致性正确 | PostgreSQL 集成测试 5/5，覆盖幂等、双 Worker 竞争、双客服竞争、事务回滚 | `docs/07_Evaluation.md` §6.2、`docs/06_LowLevelDesign.md` §5.4 |
| 对话质量经过独立评测 | LLM-as-Judge 7/7，五维度打分 + 4 条关键场景 pass^k=3 一致验证，评测脚本已修复"误判可漏检失败动作"的方法论问题 | `docs/07_Evaluation.md` §7 |
| 安全边界经过实际验证 | 3 个已发现并修复的业务逻辑漏洞（证据门槛缺失、金额上限缺失、风险规则隐性依赖），均有具体触发场景和回归测试 | `docs/10_ResultsReport.md` §2.1-2.3 |
| 履约不会被误报为已完成 | 批准与到账严格分离（`ExecutionPending`/`Closed`/`ExecutionFailed`），该设计在一次运维事故（Worker 未启动）中经过验证——问题被限制在"服务未启动"而非"数据被污染" | `docs/09_StrategyRollout.md` §1.3、`docs/10_ResultsReport.md` §2.4 |
| 尚未闭合的证据 | Human Escalation Rate、Average Response Time 未实测；风险阈值未经真实数据校准；LLM-judge 样本量小（7 条） | `docs/10_ResultsReport.md` §4、`docs/08_RiskDriverAnalysis.md` §2 |

---

# 3. 路线图

## 3.1 近期（对接真实数据前必须完成）

- 按 `docs/09_StrategyRollout.md` §3.2 的影子模式接入一段时间真实（或高保真
  模拟）流量，测算真实的 Human Escalation Rate 和 Average Response Time；
- 基于真实订单金额分布，重新校准 `high_refund_count`/`high_value_amount`/
  `new_account_days` 三个风险阈值（`docs/08_RiskDriverAnalysis.md` §2）；
- 扩充 LLM-as-Judge 用例规模，覆盖更多口语化/情绪化表达变体。

## 3.2 中期（放量阶段）

- 按 `docs/09_StrategyRollout.md` §3.2 的规则命中类型顺序逐步放开自动化比例，
  优先放开"未发货取消"等不依赖证据材料的规则；
- 建立风险阈值的定期复核机制（而非一次性配置），由业务/风控负责人根据
  `docs/09_StrategyRollout.md` §3.3 责任矩阵中定义的分工持续维护；
- 接入真实支付/仓储/订单系统，替换 Worker 当前的模拟执行。

## 3.3 长期（`docs/01_PRD.md` 第 11 章已列出的能力扩展）

优惠券核销、售前咨询、CRM 客户画像整合、跨渠道信息接入、决策质量评估看板——
这些是范围边界之外的能力扩展，不影响当前售后决策闭环的独立交付。

