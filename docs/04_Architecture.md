# Architecture Design
# CustomerServiceDecisionAgent

> 本文档描述系统如何实现 [PRD.md](./PRD.md) 中定义的业务能力（How）。
> 详细设计（每个 Agent 的 Prompt / State / Tool / Conditional Edge）见 `docs/06_LowLevelDesign.md`。

---

# 1. 架构目标

PRD 中定义的核心业务能力是"综合多方业务信息进行决策分析"。本章说明为什么选择**多智能体协作架构**、每个组件如何拆分，以及为什么这样拆分比其他方案更合理。

## 1.1 为什么不是单 Agent + 工具调用

一种更简单的实现方式是：单个 Agent 按顺序调用查订单、查物流、查政策等工具，最后自己给出结论（ReAct 式规划）。

这种方式的问题：
- 决策逻辑和信息收集逻辑耦合在同一个 Agent 里，难以独立测试和迭代
- 无法清晰地表达"多方证据 + 置信度"这种结构化的决策依据
- 当信息来源之间出现冲突时，缺乏专门的裁决环节，容易被 LLM 自由发挥掩盖问题

因此本项目采用**多智能体协作编排架构**：将信息收集、决策分析、结果复核拆分为职责独立的 Agent，并通过 LangGraph 显式编排它们之间的协作关系。

## 1.2 架构决策：按数据来源拆分 Agent，而非按业务岗位拆分

项目设计初期曾按"客服业务岗位"拆分 Agent（分诊 / 专业解答 / 风控审核 / 情绪升级），但存在职责边界模糊、容易重叠的问题——例如"退换货"该归入风控还是解答不清晰，导致 Agent 之间的协作关系难以清晰定义。

调整为**按数据来源拆分**（Order / Logistics / Policy）后：
- 每个 Agent 的职责边界与数据边界完全对应，不会重叠
- 更贴近真实企业系统的服务拆分方式（企业系统通常围绕数据域或能力域划分微服务，而非模拟人工岗位）
- Decision Engine 的裁决真正落在"融合多方证据"上，而不是"协调岗位职责"上

---

# 2. 系统架构图

```
                Customer Request
                        │
                        ▼
                 Planner Agent
          （对应 PRD FR1 · 任务规划、状态管理）
                        │
        ┌───────────────┼────────────────┐
        ▼               ▼                ▼
   Order Agent    Logistics Agent    Policy Agent
   （订单数据）      （物流数据）      （RAG 知识库）
        │               │                │
        └───────────────┼────────────────┘
                        ▼
     Evidence Fusion Decision Engine（核心 · 对应 FR3）
                        │
                        ▼
              Reflection Agent（对应 FR4）
                        │
                        ▼
               Response Agent（对应 FR5）
                        │
                        ▼
         Human Escalation（Optional · 对应 FR6）
```

---

# 3. 组件设计

## 3.1 Planner Agent

**对应业务能力**：PRD FR1 Task Planning

**职责**：解析用户请求，识别业务类型（退款 / 换货 / 物流 / 咨询），规划本次请求需要调用的数据 Agent 组合，维护整体会话状态。

**不做**：不做业务判断，不直接输出决策。

## 3.2 数据 Agent（对应业务能力：PRD FR2 Business Information Retrieval）

| Agent | 数据源 | 职责 | 明确不做 |
|---|---|---|---|
| Order Agent | Orders 表 | 查询订单状态、付款状态、商品信息、VIP 信息，输出带置信度的结构化证据 | 不查物流、不判断政策 |
| Logistics Agent | Logistics 表 | 查询物流状态、配送、签收信息，输出带置信度的结构化证据 | 不查订单、不判断政策 |
| Policy Agent | 企业知识库（RAG） | 检索退款政策、会员政策等规则文本，输出匹配条款与置信度 | 不做最终裁决 |

**统一输出格式**：

```json
{
  "field": "...",
  "evidence": "...",
  "confidence": 0.0
}
```

## 3.3 Evidence Fusion Decision Engine

**对应业务能力**：PRD FR3 Decision Analysis

**这是本系统的核心组件**，实现方式区别于"多数投票"式的简单仲裁。

**设计原则**：Decision Engine 的输出 = **业务规则（Business Rules） + 结构化证据（Evidence） + 置信度（Confidence）** 的融合结果，而不是让 LLM 自由裁量。

```
Order Agent 证据 + 置信度
Logistics Agent 证据 + 置信度        →   Business Rules（优先级权重、硬性条款）
Policy Agent 证据 + 置信度                        │
                                                    ▼
                                          Evidence Fusion
                                                    │
                                                    ▼
                                     Decision + Confidence + Reason Chain
```

**业务规则参与决策的方式**（避免"LLM 随意判断"）：
- 硬性规则优先：如政策条款存在明确禁止性条件（如"伪劣商品不退"），该条件一票否决，不参与置信度加权
- 加权规则：如物流异常、VIP 等级等因素按预设权重参与置信度计算
- 具体权重规则与阈值设计见 `docs/05_DecisionRules.md`（待补充）

**示例输出**：
```json
{
  "decision": "Approve",
  "confidence": 0.82,
  "reason": [
    "物流异常（Logistics Agent，置信度0.75）",
    "商品已签收（Order Agent，置信度0.95）",
    "符合退款政策第2条例外条款（Policy Agent，置信度0.90）"
  ],
  "rule_hits": ["Refund Policy #2 Exception Clause"]
}
```

## 3.4 Reflection Agent

**对应业务能力**：PRD FR4 Result Verification

**触发条件（Reflection Trigger）**，任一命中即触发复核，不是仅看置信度：

| 触发类型 | 说明 |
|---|---|
| ① Confidence Trigger | Decision Engine 输出置信度低于预设阈值 |
| ② Rule Conflict Trigger | 各数据 Agent 提供的证据之间存在矛盾（如订单显示已签收，物流显示异常） |
| ③ High-risk Business Rule Trigger | 即使置信度很高，命中业务风险规则仍需复核：大额退款 / VIP 客户 / 疑似欺诈 / 敏感商品类别 |

**复核逻辑**：重新审视证据链是否存在矛盾或遗漏；若复核后仍无法给出高置信度决策，触发人工升级。

## 3.5 Response Agent

**对应业务能力**：PRD FR5 Response Generation

基于 Decision Engine（或复核后 Reflection Agent）的最终结果，生成面向客户的自然语言回复，附带关键决策依据。

## 3.6 Human Escalation

**对应业务能力**：PRD FR6 Human Escalation

触发后，将完整证据链和决策过程（而非仅原始对话）转交人工客服。

---

# 4. 技术选型

| 模块 | 选型 | 选型理由 |
|---|---|---|
| 智能体编排框架 | **LangGraph** | 基于有向图 + 条件边的状态化编排，内置 checkpointing，支持状态回溯，天然贴合"决策过程可追溯"的业务要求 |
| 模型调用方式 | **OpenAI SDK 调用 DeepSeek（OpenAI 兼容接口）** | DeepSeek API 定价低，适合高频调试与多轮实验；接口兼容 OpenAI SDK，后续切换模型成本低 |
| 后端服务 | FastAPI | 承载对话接口与状态查询接口 |
| 前端展示 | Streamlit | 展示"客户输入 → 多 Agent 证据收集 → Decision Engine 融合裁决 → Reflection → 最终响应"的可视化流程 |
| 知识库 | Markdown + ChromaDB | Policy Agent 的 RAG 检索来源，可扩展向量检索 |
| 数据库 | JSON Mock Database | 模拟企业订单 / 物流 / 客户数据，详见 `docs/02_DataDesign.md` |

---

# 5. 数据来源

| 数据集 | 承担角色 | 说明 |
|---|---|---|
| [DeepUtteranceAggregation](https://github.com/cooelf/DeepUtteranceAggregation) | 客户对话输入 | 真实电商多轮对话语料，作为客户请求的剧本来源 |
| [CrossWOZ](https://coai.cs.tsinghua.edu.cn/tools/3) | 标注框架参考 | 标注了对话意图与状态，用于指导抽样数据的二次标注体系设计 |
| Enterprise Mock Database（自建） | Order Agent / Logistics Agent 数据源 | JSON 格式模拟订单、物流、客户、商品数据 |
| Knowledge Base（自建） | Policy Agent 数据源 | Markdown 格式的退款 / 物流 / VIP 政策文档 |

---

# 6. Demo 技术演示流程

对应 PRD 第 9 章业务场景（Case 1-4），技术层面展示完整决策链路：

```
✓ Planner Agent（规划：需要 Order + Logistics + Policy 三方证据）
        ↓
✓ Order Agent（订单已签收，置信度 0.95）
✓ Logistics Agent（物流轨迹显示异常滞留，置信度 0.75）
✓ Policy Agent（超7天条款存在例外情形，置信度 0.90）
        ↓
✓ Evidence Fusion Decision Engine（→ Approve，综合置信度 0.82）
        ↓
✓ Reflection Agent（置信度达标，无 Trigger 命中，无需复核）
        ↓
✓ Response Agent（生成客户回复）
```

借助 LangGraph 的 checkpointing，每一步中间状态都会被存下来并展示，demo 呈现的不是"最后给了个答案"，而是"每一步证据收集与融合裁决的完整链路"。

---
