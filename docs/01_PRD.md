# Product Requirements Document (PRD)
# CustomerServiceDecisionAgent

> 本文档只描述业务问题、用户需求与产品能力（What），不涉及技术实现方式（How）。
> 技术架构设计见 [04_Architecture.md](./04_Architecture.md)。

---

# 1. 项目背景（Project Background）

随着电商平台业务规模不断扩大，客服团队需要处理大量重复性的售后请求，例如退款申请、物流咨询、商品损坏、订单异常等。

传统客服主要依赖人工处理，存在以下问题：

- 重复咨询占比高，人工成本较高
- 不同客服之间处理标准不统一
- 查询订单、物流、退款政策需要频繁切换多个系统
- 复杂案例容易出现误判，需要资深客服介入

传统的自动化客服系统通常是"单一问答链路"：一次查询、一个答案，缺乏对多方信息的综合判断能力。

```
传统客服 Workflow

Customer Request
       │
       ▼
   单一查询
       │
       ▼
   直接回答
```

本项目希望改变这种模式：

```
CustomerServiceDecisionAgent

Customer Request
       │
       ▼
  多方业务信息收集
       │
       ▼
   决策分析（Decision）
       │
       ▼
   合理性复核（Reflection）
       │
       ▼
  客户回复 / 人工升级
```

本项目拟设计一个具备企业级业务决策能力的客服决策系统，模拟真实企业客服工作流，实现从"信息收集"到"综合决策"再到"复核校验"的完整业务闭环。

---

# 2. 项目目标（Project Objectives）

本项目目标不是开发一问一答的聊天机器人，而是构建一套具备**综合决策能力**的企业级客服业务系统。

系统能够：

- 理解用户售后需求
- 自动规划需要收集哪些业务信息
- 综合订单、物流、政策等多方信息进行决策分析
- 输出可解释的决策结果（结论 + 依据 + 置信度）
- 对置信度不足或存在业务风险的情况进行复核
- 必要时升级人工客服，并同步完整的决策依据
- 自动生成专业、可解释的客服回复

## 2.1 Success Metrics（成功指标）

| 指标 | 目标 | 说明 |
|---|---|---|
| Decision Accuracy（决策准确率） | > 90% | 系统决策结果与评估用例预期结果的一致比例，基于第 9 章业务场景及扩展 Case Database 评估 |
| Human Escalation Rate（人工升级率） | < 15% | 触发人工升级的请求占比，过高说明系统自动化程度不足 |
| Average Response Time（平均响应时长） | < 5s | 对应第 7 章非功能需求中的响应速度要求 |
| Workflow Success Rate（流程完整执行率） | > 95% | 请求从任务规划到最终回复（或人工升级）能够无异常走完全流程的比例 |

> 以上指标将在 `docs/07_Evaluation.md` 中给出具体测评方法与实测结果

---

# 3. 项目范围（Project Scope）

## 第一阶段支持

本项目第一版支持以下业务：

- 退款申请
- 物流查询
- 商品损坏申请
- 商品未收到
- 换货申请
- 售后咨询

## 暂不支持

为了控制项目规模，第一版暂不支持：

- 在线支付
- 商品推荐
- 售前咨询
- 优惠券发放
- 投诉升级
- ERP 库存管理

---

# 4. 用户角色（Stakeholders）

## Customer

电商平台消费者

主要需求：
- 查询订单
- 查询物流
- 申请退款
- 售后咨询

## Customer Service

客服人员

主要需求：
- 自动完成标准业务
- 查看系统决策过程与依据
- 接管复杂案例

## Customer Service Manager

客服主管

主要需求：
- 查看系统决策结果
- 审核人工升级案例
- 调整业务规则

---

# 5. 业务能力设计（Business Capability Design）

系统提供以下端到端业务能力：

```
Customer Request
        │
        ▼
Task Planning（任务规划）
        │
        ▼
Business Information Collection（多方业务信息收集）
        │
        ▼
Decision Analysis（决策分析）
        │
        ▼
Result Verification（结果复核）
        │
        ▼
Customer Response / Human Escalation（客户回复 / 人工升级）
```

各能力模块的业务定义见第 6 章功能需求。技术上如何拆分实现这些能力（例如是否采用多智能体架构、每个组件的内部设计），见 [04_Architecture.md](./04_Architecture.md)。

---

# 6. 功能需求（Functional Requirements）

## FR1 Task Planning（任务规划）

系统需要理解用户当前的业务诉求（退款 / 换货 / 物流查询 / 售后咨询），并规划完成该业务所需要收集的信息类型。

## FR2 Business Information Retrieval（业务信息收集）

系统需要具备综合查询以下业务信息的能力：
- 订单信息（状态、付款、商品、会员等级）
- 物流信息（配送状态、签收、异常情况）
- 政策信息（退款政策、会员政策等企业规则）

该能力由多个专职信息收集模块实现，详细设计见《Architecture.md》。

## FR3 Decision Analysis（决策分析）

系统需要综合以上业务信息，给出最终决策，而不是仅依据单一信息源做判断。

决策输出应包含：
- **结论**（Decision）：如批准退款 / 拒绝退款 / 需人工介入
- **依据**（Evidence）：支撑该结论的关键业务信息
- **置信度**（Confidence）：系统对该决策结果的把握程度

**决策依据要求**：系统的判断不能仅由模型自由发挥，必须结合企业既定业务规则（如退款政策条款、VIP 权益规则）与收集到的业务证据共同得出结论，确保决策有据可查、不是"模型随意判断"。

## FR4 Result Verification（结果复核）

以下情况需要触发复核，而不是直接输出结果：

1. **置信度不足**：决策置信度低于预设阈值
2. **信息冲突**：不同信息来源之间存在矛盾（如订单显示已签收，但物流显示异常滞留）
3. **业务风险触发**：即使置信度很高，涉及以下情形仍需复核或人工介入：
   - 大额退款
   - VIP 客户
   - 疑似欺诈行为
   - 敏感商品类别

复核后仍无法给出可靠结论的，转交人工客服，并同步完整的决策依据（而非仅转交原始对话）。

## FR5 Response Generation（回复生成）

系统需要基于决策结果（或复核后结果），自动生成面向客户的自然语言回复。

回复要求：
- 礼貌
- 清晰
- 可解释（附带关键依据，而非只给结论）

## FR6 Human Escalation（人工升级）

当系统无法给出可靠决策，或触发业务风险规则时，需要将完整的信息收集结果与决策依据转交人工客服，减少人工客服重新排查信息的成本。

---

# 7. 非功能需求（Non-functional Requirements）

## 响应速度

普通请求：≤ 5 秒

## 可解释性

所有决策均需要输出：
- Decision（结论）
- Evidence（依据）
- Confidence（置信度）

## 可扩展性

系统需要支持后续新增业务能力（如优惠券核销、售前咨询、CRM 数据接入），且新增能力不应要求重新设计整体业务流程。

## 可维护性

业务规则（如置信度阈值、风险触发条件）应当可配置，业务人员可自主调整，无需修改核心系统代码。

---

# 8. 数据来源（Data Sources）

## 客户请求与评估输入

核心测试输入由场景化 Case Database 派生，并通过 `request_id` 关联 Service Request。

## 企业业务数据（Mock）

模拟企业内部数据，包括订单、物流、客户、商品信息。

## 企业知识库

包含退款政策、物流政策、会员政策等规则文档，供决策分析环节检索使用。

> 具体 Mock Database 与评估输入设计见 [docs/04_Architecture.md](./04_Architecture.md) 与 [docs/02_DataDesign.md](./02_DataDesign.md)。

---

# 9. 业务场景示例（Business Cases）

> 以下为业务能力验证用例，用于说明系统在不同信息组合下应给出的合理决策方向。技术实现层面的 Demo 演示流程见 [04_Architecture.md](./04_Architecture.md)。

**Case 1：商品损坏，信息一致，应批准**
客户反馈商品收到时已损坏，订单显示已签收，物流无异常，符合退款政策关于"商品损坏"的条款 → 决策：批准退款。

**Case 2：VIP 客户 + 物流丢失，应批准并优先处理**
VIP 客户反馈包裹未收到，物流信息显示丢件 → 决策：批准退款，且因 VIP 身份触发优先处理标记。

**Case 3：超过退货时效，应拒绝**
客户申请无理由退货，订单显示已签收超过 30 天，政策明确超期不支持退货 → 决策：拒绝。

**Case 4：信息冲突，应转复核**
政策显示已超过退款时效应拒绝，但物流信息显示存在配送异常（可能非客户责任）→ 决策：触发复核，而非直接拒绝。

---

# 10. Acceptance Criteria（验收标准）

> 与第 2.1 节 Success Metrics 对应：本节是"功能层面是否具备该能力"的验收，Success Metrics 是"具备该能力后表现好不好"的量化评估，两者在 `docs/07_Evaluation.md` 中共同构成验收依据。

- [ ] 系统能够根据客户请求类型规划所需信息收集范围
- [ ] 系统能够综合订单、物流、政策三类信息给出决策，而非仅依据单一信息源
- [ ] 决策结果包含结论、依据、置信度三要素
- [ ] 置信度不足、信息冲突、业务风险规则命中三类情况均能正确触发复核
- [ ] 复核后仍无法确定的案例能够正确转交人工，并同步决策依据
- [ ] 第 9 章四个业务场景用例均能得到符合预期方向的决策结果

---

# 11. 后续规划（Future Work）

未来计划增加的业务能力：
- 优惠券核销业务
- 售前咨询业务
- CRM 客户画像整合
- 跨渠道信息接入（如社媒投诉）
- 基于人工复核反馈的决策规则持续优化
- 决策质量评估看板

---

# 12. 项目交付物（Deliverables）

本项目最终交付内容包括：

## Documentation

- `docs/01_PRD.md` —— 产品需求文档
- `docs/02_DataDesign.md` —— 数据设计
- `docs/03_BenchmarkResearch.md` —— 标杆产品调研
- `docs/04_Architecture.md` —— 系统架构设计
- `docs/05_DecisionRules.md` —— 决策规则设计
- `docs/06_LowLevelDesign.md` —— 详细设计
- `docs/07_Evaluation.md` —— 评估报告（对应第 2.1 节 Success Metrics 实测结果）
- `docs/08_ManualTesting.md` —— 手工测试清单
- `docs/09_ResultsReport.md` —— 结果报告：系统现状、发现并修复的问题、评测结果

## Data Assets

- Enterprise Mock Database（订单 / 物流 / 客户 / 商品）
- Policy Knowledge Base（退款 / 物流 / VIP 政策文档，确定性匹配，非向量检索）
- Evaluation Test Cases（以 `request_id` 关联 Mock Database，并记录预期决策与规则依据）

## System

- CustomerServiceDecisionAgent（可运行系统）
- LangGraph Multi-Agent Workflow
- Web Demo（React 客户端 + Staff 人工工作台，FastAPI 提供接口）

## Project Deliverables

- GitHub Repository
- Technical Report
- Presentation Slides

---
