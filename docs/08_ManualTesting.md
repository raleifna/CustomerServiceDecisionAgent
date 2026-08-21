# 手工测试清单

## 1. 启动准备

在 `.env` 中至少配置：

```bash
STAFF_API_KEYS={"reviewer-token-a":{"staff_id":"agent_a","roles":["reviewer"]},"reviewer-token-b":{"staff_id":"agent_b","roles":["reviewer"]}}
```

启动客户前端、Staff 工作台、API 和 PostgreSQL：

```bash
docker compose up --build
```

- 客户端：`http://localhost:3001`
- Staff 工作台：`http://localhost:3001/staff`
- API 文档：`http://localhost:8000/docs`

重复测试创建类场景前需要使用新订单，或者明确清理测试数据库。`docker compose down -v`
会删除本项目 PostgreSQL 数据卷，只能在确认不需要已有本地数据时使用。

**注意**：`action-worker` 服务挂了 `profiles: ["execution"]`，不带 profile 的
`docker compose up` 不会启动它，也不会重启一个已存在但已退出的旧容器。凡是要
验证"批准后是否真的执行了退款/取消/换货"，必须用：

```bash
docker compose --profile execution up -d
```

否则申请会一直停在 `ExecutionPending`，看起来批准成功、实际上从未真正执行——
这个坑在项目实际开发过程中真的踩过一次，见 `docs/09_ResultsReport.md`。

## 2. 客户端与订单项

| 编号 | 操作 | 测试数据 | 预期结果 |
|---|---|---|---|
| UI-01 | 选择客户和订单 | `CUS0007` / `ORD2026080007` | 订单卡片显示一个订单项：默认规格、单价 `99.00`、购买 `3` 件、实付 `297.00`、已出库 `2` 件。 |
| UI-02 | 查看双商品订单 | `CUS0021` / `ORD2026080027` | 显示两个 SKU：`PRO0001-BLACK`（699 元）和 `PRO0006-WHITE-42`（399 元）；订单实付合计为 1098 元。 |
| UI-03 | 双商品订单发起售后但不指定 SKU | 在 `ORD2026080027` 会话输入“我想退款” | Agent 不默认选择整单，回复中列出两个 SKU 并要求选择商品。 |
| UI-04 | 补充选择第二个 SKU | 接着输入“SKU PRO0006-WHITE-42，退1件” | 创建的售后申请只关联白色 42 码订单项；批准金额按购买快照计算为 399 元，而不是整单 1098 元。 |
| UI-05 | 验证多商品未发货取消动作 | 完成 UI-04 后查看响应和数据库 | 申请进入 `ExecutionPending`；任务类型为 `CancelOrderItem`，载荷包含所选 `order_item_id`，不能是 `CancelOrder`。 |
| UI-06 | 单商品未发货取消 | `CUS0021` / `ORD2026080021` 输入“我想退款” | 批准金额 359 元；任务类型仍为 `CancelOrder`。 |

可用下面的 SQL 核对 UI-04/UI-05：

```sql
SELECT sr.request_id, sr.target_order_item_id, sr.workflow_status,
       sri.requested_quantity, sri.approved_quantity, sri.approved_amount
FROM service_requests sr
JOIN service_request_items sri USING (request_id)
WHERE sr.order_id = 'ORD2026080027';

SELECT action_type, workflow_status, payload, idempotency_key
FROM action_tasks
WHERE request_id = '<上一步查到的 request_id>';
```

## 3. Staff 人工审核闭环

| 编号 | 操作 | 测试数据 | 预期结果 |
|---|---|---|---|
| ST-01 | 不输入 Token 打开 `/staff` | 无 | 队列不加载，页面提示输入 Staff API Token。 |
| ST-02 | 输入错误 Token | 任意错误字符串 | API 返回 401，不能查看工单和证据。 |
| ST-03 | 输入 `reviewer-token-a` | `.env` 中的配置 | 页面显示服务端身份 `agent_a`，而不是允许手填处理人。 |
| ST-04 | 创建高额人工审核申请 | 客户端选择 `CUS0010` / `ORD2026080010`，输入“这件商品不想要了，申请退款1件” | 大额风险规则生效，申请进入人工队列，不能自动退款。 |
| ST-05 | 查看人工工单详情 | 打开 ST-04 工单 | 显示订单项、SKU、成交单价和“本次申请 1 件”，目标订单项使用高亮样式。 |
| ST-06 | 未领取直接提交 | 不点击“领取工单” | 提交按钮不可用；直接调用 API 也应返回 409。 |
| ST-07 | 领取并批准 | 点击领取，批准数量 `1`、金额 `12999` | 申请进入 `ExecutionPending`，生成 `decision_records`、`action_tasks` 和 `outbox_events`，而不是直接标记退款成功。 |
| ST-08 | 两名客服竞争领取 | 普通窗口使用 token A，无痕窗口使用 token B，同时打开同一工单并领取 | 只有一人领取成功；另一人收到 409，不能覆盖审批结果。 |
| ST-09 | 非法人工金额 | 退款批准但金额留空，或批准数量超过申请数量 | 服务端返回 422，不写入裁决记录和履约任务。 |

## 4. 自动化回归与真实数据库测试

| 编号 | 命令 | 预期结果 |
|---|---|---|
| AT-01 | `python -m unittest discover -s tests -v` | 普通单元测试通过；没有配置测试库时 PostgreSQL 集成测试明确显示 skipped。 |
| AT-02 | `python scripts/evaluation/run_evaluation.py` | 34/34 通过。 |
| AT-03 | `docker compose --profile test up integration-tests --build --abort-on-container-exit --exit-code-from integration-tests` | 5 个 PostgreSQL 集成测试通过，包括双 Worker 和双 Staff 并发领取。 |
| AT-04 | `npm --prefix src/frontend run build` | TypeScript 检查及 Vite 生产构建成功。 |
| AT-05 | `DATABASE_URL=... python scripts/evaluation/run_llm_judge.py`（需配置 `API_KEY`） | 7 条用例全部通过；`pass_k=3` 的用例三次均通过；确认没有 `action_status=Failed` 却仍判 PASS 的情况。 |

## 5. 重点观察的错误结果

出现以下任一结果都应视为失败：

- 多商品订单没有要求选择 SKU；
- 退款金额使用商品当前目录价而不是订单项成交快照；
- 只取消一个订单项却创建 `CancelOrder`；
- 两名客服都能领取或裁决同一工单；
- 自动或人工批准后，在外部履约尚未成功时直接显示 `Closed`；
- 同一申请产生两个相同履约任务或两个 `ActionTaskCreated` Outbox 事件。
