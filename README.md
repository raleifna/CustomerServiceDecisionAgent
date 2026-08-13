# CustomerServiceDecisionAgent

### 多智能体协作任务编排框架设计

> 启衡智能科技（教学案例）· 智能体（Agent）研发工程师高级实训项目
> 场景方向：电商客服多智能体协作决策编排

```
传统客服 Workflow                    CustomerServiceDecisionAgent

Customer Request                     Customer Request
       │                                    │
       ▼                                    ▼
   单一查询                          Multiple Evidence（多方证据收集）
       │                                    │
       ▼                                    ▼
   直接回答                          Evidence Fusion Decision Engine
                                             │
                                             ▼
                                         Reflection
                                             │
                                             ▼
                                   Response / Human Escalation
```

一个电商客服场景下的多智能体决策编排系统：多个数据 Agent（订单 / 物流 / 政策）各自产出带置信度的证据，由 **Evidence Fusion Decision Engine** 融合业务规则、证据与置信度完成裁决——而非简单投票——最终给出可解释、可追溯的客服响应路径。基于 **LangGraph** 实现有状态的图编排。

## Architecture Preview（系统层次预览）

```
Frontend（Streamlit）
        │
        ▼
FastAPI（接口层）
        │
        ▼
LangGraph（多 Agent 编排层）
        │
        ▼
Tools（Order / Logistics / Policy 查询工具）
        │
        ▼
Mock Enterprise Backend（订单/物流/客户数据）+ Knowledge Base（政策文档）
```

多 Agent 内部编排（Planner → 数据 Agent → Decision Engine → Reflection → Response）的完整设计见 [docs/04_Architecture.md](./docs/04_Architecture.md)。

## 文档导航

| 文档 | 内容 |
|---|---|
| [docs/01_PRD.md](./docs/01_PRD.md) | 产品需求文档：业务背景、用户需求、功能需求、Success Metrics（What），不涉及技术实现 | 
| docs/02_DataDesign.md | Enterprise Mock Database Schema、数据标注方案 |
| docs/03_BenchmarkResearch.md | 标杆产品调研（Zendesk、Intercom、Agentforce 等） | 
| [docs/04_Architecture.md](./docs/04_Architecture.md) | 系统架构设计：多 Agent 拆分理由、Decision Engine 设计、技术选型（How） | 
| docs/05_DecisionRules.md | Evidence Fusion 权重规则、Reflection 触发阈值 | 
| docs/06_LowLevelDesign.md | 各 Agent 的 Prompt / State / Tool / Conditional Edge 详细设计 | 
| docs/07_Evaluation.md | 测试用例、评估指标、Benchmark 结果（对应 PRD Success Metrics） |

## 技术栈速览

LangGraph · OpenAI SDK（调用 DeepSeek API）· FastAPI · Streamlit · ChromaDB

详细选型理由见 [Architecture.md 第4章](./docs/04_Architecture.md#4-技术选型)。

## Success Metrics（对应 docs/01_PRD.md 第 2.1 节）

| 指标 | 目标 |
|---|---|
| Decision Accuracy | > 90% |
| Human Escalation Rate | < 15% |
| Average Response Time | < 5s |
| Workflow Success Rate | > 95% |

---

## 项目结构

```
CustomerServiceDecisionAgent/
├── README.md
├── docs/
│   ├── 01_PRD.md
│   ├── 02_DataDesign.md          
│   ├── 03_BenchmarkResearch.md   
│   ├── 04_Architecture.md       
│   ├── 05_DecisionRules.md       
│   ├── 06_LowLevelDesign.md      
│   └── 07_Evaluation.md          
├── src/
│   ├── backend/
│   └── frontend/
├── data/
│   ├── mock_db/
│   └── knowledge_base/
└── demo/
```

---

## 待补充文档占位

<!-- docs/02_DataDesign.md：Enterprise Mock Database Schema、数据标注方案 -->
<!-- docs/03_BenchmarkResearch.md：标杆产品调研（Zendesk、Intercom、Agentforce 等） -->
<!-- docs/05_DecisionRules.md：Evidence Fusion 权重规则、Reflection 触发阈值 -->
<!-- docs/06_LowLevelDesign.md：各 Agent 的 Prompt / State / Tool / Conditional Edge 详细设计 -->
<!-- docs/07_Evaluation.md：测试用例、评估指标、Benchmark 结果 -->