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

## 快速开始：启动与体验 Demo

### 1. 准备环境变量

```bash
cp .env.example .env
```

编辑 `.env`，至少填写：

```bash
API_KEY=你的 DeepSeek API Key
BASE_URL=https://api.deepseek.com
MODEL=deepseek-chat
ENABLE_LLM_RESPONSE=true
STAFF_API_KEYS={"reviewer-token-a":{"staff_id":"agent_a","roles":["reviewer"]}}
```

### 2. 一键启动完整系统

```bash
docker compose --profile execution up --build -d
```

这条命令会启动 PostgreSQL、后端 API、前端和履约 Worker 四个服务。

> **必须带 `--profile execution`**：履约 Worker（负责真正执行退款/取消订单/换货）
> 默认不随 `docker compose up` 启动，不带这个参数的话，所有"已批准"的申请会一直
> 停在 `ExecutionPending`，看起来批准成功、实际上从未真正执行。

首次启动需要构建镜像，等待约 1-2 分钟；`docker compose ps` 确认四个容器都是
`Up`/`healthy` 状态后即可访问：

- **客户端**：http://localhost:3001
- **Staff 人工工作台**：http://localhost:3001/staff
- **API 文档（Swagger UI）**：http://localhost:8000/docs

### 3. 体验一条完整的自动裁决路径

1. 打开客户端 http://localhost:3001，选择任意 Mock 客户（如 `CUS0021`）；
2. 选择一个订单，在对话框输入"我不想要了，帮我退款"（未发货订单会走"未发货取消"
   自动批准路径）；
3. 确认订单后，Agent 会创建售后申请并直接给出裁决结果（批准/拒绝/需要更多证据），
   右侧面板可以看到每个 Agent 的处理轨迹；
4. 若命中大额或高风险规则，申请会转人工——切到 Staff 工作台，用 `.env` 里配置的
   Token 登录，领取工单后可以查看完整证据链并人工裁决。

更多场景（质量问题证据上传与复核、多商品订单选择 SKU、超过 30 天提交上限转
人工确认等）见 `docs/07_Evaluation.md` 中的用例说明，或直接在客户端里自由对话尝试。

### 4. 停止与重置

```bash
docker compose down        # 停止，保留数据库数据卷
docker compose down -v     # 停止并清空数据卷（重新从种子数据开始）
```

---

## Architecture Preview（系统层次预览）

```
Frontend（React + Vite，客户端 + Staff 工作台）
        │
        ▼
FastAPI（接口层：/chat、/customers、/service-requests、/staff）
        │
        ▼
LangGraph（多 Agent 编排层：Order/Logistics/Evidence/Policy → Gate → Decision → Reflection → Response）
        │
        ▼
Repository 层（Order / Logistics / Policy / Evidence / Decision Lifecycle 查询与写入）
        │
        ▼
Mock Enterprise Backend（订单/物流/客户数据）+ Knowledge Base（政策文档）
        │
        ├── PostgreSQL（会话状态、Service Request 生命周期、履约操作记录）
        └── JSON / Markdown（固定案例事实 / 政策 / 声明式裁决规则）
```

多 Agent 内部编排（Router → 数据 Agent → Evidence Gate → Decision Engine → Reflection → Response）的完整设计见 [docs/04_Architecture.md](./docs/04_Architecture.md)，各 Agent/节点的输入输出契约见 [docs/06_LowLevelDesign.md](./docs/06_LowLevelDesign.md)。

## 文档导航

| 文档 | 内容 |
|---|---|
| [docs/01_PRD.md](./docs/01_PRD.md) | 产品需求文档：业务背景、用户需求、功能需求、Success Metrics（What），不涉及技术实现 |
| [docs/02_DataDesign.md](./docs/02_DataDesign.md) | Mock Database、Knowledge Base、评估用例与可选语言样本设计 |
| [docs/03_BenchmarkResearch.md](./docs/03_BenchmarkResearch.md) | 标杆产品调研（Zendesk、Intercom、Agentforce、阿里云等）及对本项目架构决策的印证 |
| [docs/04_Architecture.md](./docs/04_Architecture.md) | 系统架构设计：多 Agent 拆分理由、Decision Engine 设计、技术选型（How） |
| [docs/05_DecisionRules.md](./docs/05_DecisionRules.md) | 声明式裁决规则（`decision_rules.json`）与业务参数（`business_rules.json`）的运行方式 |
| [docs/06_LowLevelDesign.md](./docs/06_LowLevelDesign.md) | 各 Agent / Graph 节点的 State 输入输出、Conditional Edge 与关键源文件 |
| [docs/07_Evaluation.md](./docs/07_Evaluation.md) | 确定性回归套件 + LLM-as-judge 质量套件：情景推演、压力测试、用例设计与最新基线 |
| [docs/08_RiskDriverAnalysis.md](./docs/08_RiskDriverAnalysis.md) | 关键驱动与风险归因分析：决策结果差异的驱动因子、高影响/高不确定场景 |
| [docs/09_StrategyRollout.md](./docs/09_StrategyRollout.md) | 策略方案与落地路径设计：备选方案比较、推荐路径、责任矩阵 |
| [docs/10_ResultsReport.md](./docs/10_ResultsReport.md) | 结果报告：系统现状、发现并修复的业务逻辑问题、评测结果 |
| [docs/11_DecisionReportRoadmap.md](./docs/11_DecisionReportRoadmap.md) | 决策汇报与落地路线图：管理层结论、关键证据、路线图 |

## 技术栈速览

LangGraph · OpenAI SDK（调用 DeepSeek API）· FastAPI · PostgreSQL · React + Vite

详细选型理由见 [Architecture.md 第8章](./docs/04_Architecture.md#8-技术选型)。

## Success Metrics（对应 docs/01_PRD.md 第 2.1 节）

实测结果见 [docs/10_ResultsReport.md](./docs/10_ResultsReport.md)。

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
├── docs/                          # 见上方"文档导航"，01-11
├── src/
│   ├── backend/
│   │   ├── agents/                # Order/Logistics/Evidence/Policy/Reflection/Response/Router 等节点
│   │   ├── actions/                # 履约动作：Fulfillment/HumanHandoff/LogisticsChase/ServiceRequest/Withdraw
│   │   ├── api/                    # FastAPI 应用、Schema、Staff 鉴权
│   │   ├── config/                 # business_rules.py 等运行时配置加载
│   │   ├── decision/                # engine.py + rule_engine.py（声明式裁决）
│   │   ├── graph/                  # LangGraph 编排、State、Evidence Gate、Finalization
│   │   ├── llm/                     # LLM 客户端与调用遥测
│   │   ├── repositories/            # 数据访问层（Postgres / Mock JSON 回退）
│   │   └── services/                 # 履约执行、证据上传等服务
│   └── frontend/
│       └── src/                    # App.tsx（客户端）、StaffConsole.tsx（人工工作台）
├── scripts/
│   ├── evaluation/                 # run_evaluation.py（确定性）、run_llm_judge.py（LLM-as-judge）
│   └── workers/                    # run_action_worker.py（履约任务异步执行）
├── tests/                          # 单元测试 + tests/integration（真实 Postgres）
├── data/
│   ├── mock_db/                    # Enterprise Mock Database（JSON）
│   ├── knowledge_base/             # 政策 Markdown
│   ├── business_rules.json         # 阈值、金额、次数等环境参数
│   ├── decision_rules.json         # 声明式裁决规则
│   └── evaluation/                 # 确定性用例 + LLM-judge 用例
└── docker-compose.yml               # postgres / backend / frontend / action-worker（execution profile）/ integration-tests（test profile）
```

---

## 后端 API 开发补充说明

一键启动方式见上方"快速开始"。以下是不用 Docker、直接跑 Python 的本地开发方式：

```bash
python -m pip install -r requirements.txt
uvicorn src.backend.api.main:app --reload
```

直接运行 Python 且未设置 `DATABASE_URL` 时，系统会回退到内存会话和 JSON
履约记录，仅适合本地调试；需要持久化时，请按 `.env.example` 配置本地数据库连接。

Docker Compose 启动 PostgreSQL 时会自动创建 LangGraph Checkpoint 表及
`fulfillment_requests` 表。数据库数据保存在命名卷中，重启容器后不会丢失。
默认宿主机端口为 `15433`，可在 `.env` 中通过 `POSTGRES_PORT` 修改。

## 可靠履约 Worker

自动批准只创建幂等履约任务，不会把“批准”误报成“退款已经完成”。配置外部服务后，
可以启动独立 Worker：

```bash
PAYMENT_REFUND_URL=http://payment-service/refunds \
WAREHOUSE_EXCHANGE_URL=http://warehouse-service/exchanges \
ORDER_CANCEL_URL=http://order-service/cancellations \
docker compose --profile execution up --build
```

Worker 使用 `Idempotency-Key` 调用外部服务，并按指数退避重试。成功后申请从
`ExecutionPending` 进入 `Closed`；超过最大尝试次数后进入 `ExecutionFailed`，
不会修改原有批准或拒绝结论。任务领取使用 PostgreSQL `SKIP LOCKED`，支持多个
Worker 实例并行消费。每次任务创建、重试、成功或失败都会在同一事务中写入
Outbox 事件。

## 人工工作台认证与工单并发

`/staff/*` 不再信任前端提交的处理人姓名。请在 `.env` 中配置不透明 Token 到
服务端员工身份和角色的映射：

```bash
STAFF_API_KEYS='{"replace-with-long-random-token":{"staff_id":"agent_001","roles":["reviewer"]}}'
```

支持 `viewer`、`reviewer` 和 `admin`。工作台中输入 Token 后，服务端解析真实
`staff_id`。人工裁决前必须领取工单；领取产生 15 分钟租约和版本号，其他客服不能
抢占有效租约。审批提交会校验工单归属、租约和版本，防止两人同时覆盖结果。

人工批准与自动批准使用同一执行闭环：写入不可变裁决记录、创建幂等履约任务和
Outbox 事件，外部执行成功后才关闭申请。达到最大重试次数的任务会进入工作台的
“履约失败任务”队列，可由有权限的客服重新入队。领取、证据审核、人工裁决和手动
重试均记录在 `audit_logs`。

## PostgreSQL 集成测试

单元测试不会实际执行 PostgreSQL 的事务、约束和 `SKIP LOCKED`。集成测试使用
独立的临时数据库验证申请与申请项原子创建、裁决/履约/Outbox 幂等、事务回滚和
多 Worker 竞争领取：

```bash
docker compose --profile test up integration-tests \
  --build --abort-on-container-exit --exit-code-from integration-tests
```

测试数据库使用 tmpfs，结束后不保留数据。若本地已经有专用测试库，也可以设置
`TEST_DATABASE_URL` 后运行：

```bash
TEST_DATABASE_URL=postgresql://user:pass@localhost/customer_service_test \
python -m unittest discover -s tests/integration -v
```

为防止误清空开发或生产库，数据库名称必须包含 `test`，否则集成测试会跳过。

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

## 运行 LLM-as-Judge 质量评测

除确定性 Evaluation Harness 外，还有一套走真实 LLM 调用的对话质量评测（语气、
事实准确性、完整性、policy_compliance、safety 五个维度打分；关键场景额外跑
pass^k 一致性检验）：

```bash
DATABASE_URL=postgresql://customer_service:customer_service_dev@localhost:15433/customer_service \
python scripts/evaluation/run_llm_judge.py --report reports/llm_judge.json
```

会产生真实 API 调用费用，且不完全确定性；用于抓工具执行失败、语气机械等
确定性套件覆盖不到的问题类别。详见 [docs/07_Evaluation.md](./docs/07_Evaluation.md)。
