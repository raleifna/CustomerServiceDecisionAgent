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
        │
        ├── PostgreSQL（会话 Checkpoint / 履约操作记录）
        └── JSON / Markdown（固定案例事实 / 政策）
```

多 Agent 内部编排（Planner → 数据 Agent → Decision Engine → Reflection → Response）的完整设计见 [docs/04_Architecture.md](./docs/04_Architecture.md)。

## 文档导航

| 文档 | 内容 |
|---|---|
| [docs/01_PRD.md](./docs/01_PRD.md) | 产品需求文档：业务背景、用户需求、功能需求、Success Metrics（What），不涉及技术实现 | 
| [docs/02_DataDesign.md](./docs/02_DataDesign.md) | Mock Database、Knowledge Base、评估用例与可选语言样本设计 |
| docs/03_BenchmarkResearch.md | 标杆产品调研（Zendesk、Intercom、Agentforce 等） | 
| [docs/04_Architecture.md](./docs/04_Architecture.md) | 系统架构设计：多 Agent 拆分理由、Decision Engine 设计、技术选型（How） | 
| docs/05_DecisionRules.md | Evidence Fusion 权重规则、Reflection 触发阈值 | 
| docs/06_LowLevelDesign.md | 各 Agent 的 Prompt / State / Tool / Conditional Edge 详细设计 | 
| docs/07_Evaluation.md | 测试用例、评估指标、Benchmark 结果（对应 PRD Success Metrics） |

## 技术栈速览

LangGraph · OpenAI SDK（调用 DeepSeek API）· FastAPI · PostgreSQL · React

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
│   ├── knowledge_base/
│   └── dialogue_dataset/       # 保留的可选语言鲁棒性材料，不作为核心决策数据源
└── demo/
```

---

## 后端 API

复制环境变量模板并填写本地密钥（不要提交 `.env`）：

```bash
cp .env.example .env
```

使用 Docker 启动：

```bash
docker compose up --build
```

Docker Compose 会同时启动 PostgreSQL，并自动创建 LangGraph Checkpoint 表及
`fulfillment_requests` 表。数据库数据保存在命名卷中，重启容器后不会丢失。
默认宿主机端口为 `15433`，可在 `.env` 中通过 `POSTGRES_PORT` 修改。

本地启动：

```bash
python -m pip install -r requirements.txt
uvicorn src.backend.api.main:app --reload
```

直接运行 Python 且未设置 `DATABASE_URL` 时，系统会回退到内存会话和 JSON
履约记录，仅适合本地调试；需要持久化时，请按 `.env.example` 配置本地数据库连接。

接口地址：

- Health Check：`http://localhost:8000/health`
- Mock Customers：`http://localhost:8000/customers`
- Customer Orders：`http://localhost:8000/customers/{customer_id}/orders`
- Order & Logistics Detail：`http://localhost:8000/customers/{customer_id}/orders/{order_id}`
- Evidence Upload：`POST http://localhost:8000/service-requests/{request_id}/evidence`
- Evidence List：`GET http://localhost:8000/service-requests/{request_id}/evidence?customer_id={customer_id}`
- Chat API：`http://localhost:8000/chat`
- Swagger UI：`http://localhost:8000/docs`
- Agent Console：`http://localhost:3001`

多轮请求必须复用同一个 `conversation_id`：

```bash
curl -X POST http://localhost:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{
    "conversation_id": "customer-001",
    "customer_id": "CUS0021",
    "order_id": "ORD2026080021",
    "message": "订单ORD2026080021什么时候发货？"
  }'
```

`customer_id` 是会话身份边界；`order_id` 仅在从订单页进入客服时传入，且必须
属于当前客户。前端不传入意图，意图始终由 Agent 根据自然语言识别。

退款、部分退款和换货诉求会先创建 PostgreSQL `service_requests` 记录。新申请
初始状态为 `Pending`；质量问题的文字陈述不会被直接视为已验证证据，也不会
自动批准，后续需要进入证据提交与审核流程。

证据上传当前支持 JPG、PNG 和 WebP，单文件最大 10MB。后端会检查文件内容
签名而不只信任扩展名或浏览器 MIME；文件保存在 `data/uploads/`，PostgreSQL
只保存存储路径、类型、大小和审核状态等元数据。新上传证据初始状态为
`Pending`，不代表商品问题已确认。

运行 Evaluation Harness：

```bash
python scripts/evaluation/run_evaluation.py
```

前端单独开发：

```bash
cd src/frontend
npm install
npm run dev
```

Vite 开发服务器会将 `/api` 请求代理到 `http://127.0.0.1:8000`。

---

## 待补充文档占位

<!-- docs/03_BenchmarkResearch.md：标杆产品调研（Zendesk、Intercom、Agentforce 等） -->
<!-- docs/05_DecisionRules.md：Evidence Fusion 权重规则、Reflection 触发阈值 -->
<!-- docs/06_LowLevelDesign.md：各 Agent 的 Prompt / State / Tool / Conditional Edge 详细设计 -->
<!-- docs/07_Evaluation.md：测试用例、评估指标、Benchmark 结果 -->
