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

`.env.example` 里注释掉的 `DATABASE_URL` 只在**不用 Docker、本地直接运行
Python 后端**时才需要手动打开：这时数据库监听在宿主机的 `localhost:15433`
（`POSTGRES_PORT` 映射的端口）。用 `docker compose` 启动时不需要管这一行——
后端容器和 Postgres 容器在同一个 Docker 网络里，`docker-compose.yml` 会把
`DATABASE_URL` 覆盖成容器间地址（host 是服务名 `postgres`，而不是
`localhost`），保持注释状态即可。

### 2. 一键启动完整系统

```bash
docker compose --profile execution up --build -d
```

这条命令会启动 PostgreSQL、后端 API、前端和履约 Worker 四个服务——**这是启动
本系统的标准方式，请始终带上 `--profile execution`，不要省略。**

系统把"裁决"和"执行"拆成了两步——Decision Engine 批准退款后，
只是把这笔任务记录为"待执行"（`ExecutionPending`），真正调用退款/取消订单/
换货接口的是一个独立进程，也就是履约 Worker。这个 Worker 在 `docker-compose.yml`
里被标记为 `profiles: ["execution"]`，如果启动时不加 `--profile execution`
（比如只运行不带参数的 `docker compose up`），Worker 就不会被启动，所有
"已批准"的申请会一直卡在"待执行"状态——客户端上看起来裁决已经完成，但退款、
取消订单、换货这些动作实际上从未被真正执行。带上 `--profile execution` 才能
体验到系统完整的闭环行为。

首次启动需要构建镜像，等待约 1-2 分钟；`docker compose ps` 确认四个容器都是
`Up`/`healthy` 状态后即可访问：

- **客户端**：http://localhost:3001
- **Staff 人工工作台**：http://localhost:3001/staff
- **API 文档（Swagger UI）**：http://localhost:8000/docs

### 3. 体验一条完整的自动裁决路径

客户端是一个简化的电商站点「ShopCare 优选」：首页 / 分类搜索 / 商品详情 / 购物车 /
我的订单 / 订单详情 / 退款售后 / 客服中心，各页面之间互相跳转（商品 ↔ 订单 ↔ 客服）。

1. 打开客户端 http://localhost:3001，点右上角「请登录」，选择任意演示账号（如 `CUS0021`）；
2. 进入「我的订单」，在订单卡片或订单详情页点「申请售后」/「联系客服」，会带着该订单
   进入客服中心；在对话框输入"我不想要了，帮我退款"（未发货订单会走"未发货取消"
   自动批准路径）；
3. 确认订单后，Agent 会创建售后申请并直接给出裁决结果（批准/拒绝/需要更多证据），
   右侧面板可以看到每个 Agent 的处理轨迹，「退款/售后」页面可以查看申请进度；
4. 若命中大额或高风险规则，申请会转人工——点顶部「人工工作台」进入 Staff 工作台，
   用 `.env` 里配置的 Token 登录，领取工单后可以查看完整证据链并人工裁决。工作台还包含
   证据审核、退货验收（确认收货 → 验收通过后生成退款任务）、履约异常重试、全部工单检索和数据看板
   （自动化率、转人工率、每日趋势、原因分布；大模型调用健康仅 admin 可见）。
5. 无理由退货批准后不会立刻退款：顾客在「我的订单 → 寄回商品」或「退款/售后」里看到退货地址并填写
   寄回运单号，Staff 在「退货验收」确认收货、验收通过后才生成原路退款任务。

更多场景（质量问题证据上传与复核、多商品订单选择 SKU、超过 30 天提交上限转
人工确认等）见 `docs/07_Evaluation.md` 中的用例说明，或直接在客户端里自由对话尝试。

> 商品页面的标题、品牌、规格、评价等展示信息来自 `data/mock_db/product_catalog.json`，
> 这是纯展示层数据；价格、品类、易碎/定制等裁决会用到的事实只来自 `products.json`，
> 商品页上的"7天无理由退货 / 易碎品保障"等服务承诺也由这些事实和 `business_rules.json`
> 推导，保证商品页的承诺与 Policy Agent 的裁决一致。购物车与结算仅用于演示，提交订单
> 不会写入订单库。

### 测试账号（推荐用来体验聊天）

评测数据集里的 21 个客户（`CUS0001`–`CUS0021`）订单日期都在 2026 年 6–8 月，而系统按**真实当天日期**
判断售后时效，所以这些订单大多已超出 7 天无理由 / 30 天质量问题的期限，且很多已有历史售后申请。
手动体验聊天请用下面这组测试账号，它们的订单日期都相对"今天"生成，登录弹窗里会优先展示并附带推荐话术，
客服中心右侧也会显示当前账号的「测试指引」：

| 账号 | 场景 | 预期结果 |
|---|---|---|
| `CUS0101` | 待发货催单 / 未发货取消 / 物流查询 / 7 天内无理由退货 | 催单成功，取消与无理由退货自动批准 |
| `CUS0102` | 质量问题、易碎品破损（需上传图片）、超 7 天无理由 | 要求补充证据；超期无理由被拒 |
| `CUS0103` | 物流停滞超 72 小时、超过承诺时间未发货 | 物流催查；未收到货等待物流核实 |
| `CUS0104` | 12999 元大额订单（铂金会员），1099 元对照订单 | 大额转人工，对照订单自动批准 |
| `CUS0105` | 新注册 12 天 + 历史退款 12 次 | 风控转人工 |
| `CUS0106` | 多商品订单、定制刻字商品 | 先选择售后商品；定制商品无理由被拒 |
| `CUS0107` | 错发、少发 | 自动批准 |

测试账号的数据在 `data/mock_db/test_accounts/`（叠加在评测数据之后，id 段 `CUS01xx` / `ORD99…` 与评测数据互不重叠，
不影响评测）。日期过期后重新生成即可，Docker 后端挂载了 `./data`，无需重新构建：

```bash
python scripts/demo/seed_test_accounts.py
```

聊天中创建的售后申请保存在 PostgreSQL；同一场景想从头再测一遍，用 `docker compose down -v` 重置数据库。

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
FastAPI（接口层：/chat、/customers、/products、/service-requests、/staff）
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
│       └── src/                    # shop/（电商站点页面）、ServiceCenter.tsx（客服中心）、StaffConsole.tsx（人工工作台）
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

## 开发与测试

- 持续集成：`.github/workflows/ci.yml` 在每次 push 到 main 和每个 PR 上运行三个任务——后端单元测试 +
  确定性评测（不调用真实 LLM）、PostgreSQL 集成测试、前端类型检查与构建；本地安装测试依赖用
  `pip install -r requirements-dev.txt`；

- 本地开发（不使用 Docker）、API 接口地址、请求示例、证据上传约束：见
  [docs/06_LowLevelDesign.md](./docs/06_LowLevelDesign.md) 第 6 章；
- 确定性回归套件、情景推演与压力测试（PostgreSQL 并发正确性）、LLM-as-Judge
  质量评测的运行方式与基线：见 [docs/07_Evaluation.md](./docs/07_Evaluation.md)；
- 履约 Worker 的外部服务对接、Staff 工作台认证与工单并发机制：见
  [docs/06_LowLevelDesign.md](./docs/06_LowLevelDesign.md) 第 5 章。
