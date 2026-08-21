# Data Design
# CustomerServiceDecisionAgent

Version: v1.4
Last Updated: 2026-08

> 本文档定义系统运行、测试与评估所需的数据资产，对应 [docs/01_PRD.md](./01_PRD.md) 第8章「数据来源」与 [docs/04_Architecture.md](./04_Architecture.md) 第5章的落地细化。

---

# 1. 数据资产总览

| 数据资产 | 用途 | 来源 | 本文档章节 |
|---|---|---|---|
| Enterprise Mock Database | Order Agent / Logistics Agent 查询数据源 | 自建 JSON | 第2章 |
| Knowledge Base | Policy Agent 确定性匹配数据源（非向量检索） | 自建 Markdown | 第3章 |
| Evaluation Cases | 保存可复现的输入、预期决策与规则依据 | 基于 Mock Database 与政策自建 | 第4章 |

---

# 2. Enterprise Mock Database

## 2.1 设计原则

- 不依赖真实电商平台数据，采用 JSON 文件模拟企业数据库表
- 每张表字段设计以"够支撑 Decision Engine 做出可解释判断"为准，不追求还原真实电商系统的完整字段
- 各表通过 `order_id` / `customer_id` / `request_id` 关联，模拟真实数据库的外键关系
- 数据需覆盖第 5 章业务场景（正常 / 异常 / 冲突）所需的所有组合，不是随机生成后凑数量

## 2.2 表结构

### Orders（订单表）

> 借鉴 Olist（巴西真实电商数据集）的建模思路：不用单一模糊的 `status` 枚举字段概括订单状态，而是用多个时间戳字段记录订单生命周期的关键节点。这样"是否延迟""延迟几天"就是可计算的派生值，而不是人工写死的标签，Order Agent / Logistics Agent 输出的证据也就有真实依据可循，而不是凭空判断。

| 字段 | 类型 | 说明 |
|---|---|---|
| order_id | string | 订单唯一标识，如 `"10001"` |
| customer_id | string | 关联 Customers 表 |
| product_id | string | 关联 Products 表 |
| quantity | integer | 客户下单数量，用于金额计算及与实际出库数量核对 |
| shipped_product_id | string \| null | 仓库实际出库的商品 ID，关联 Products 表；未发货为 null。正常情况下等于 `product_id`，不相等时表示仓库错发 |
| shipped_quantity | integer \| null | 仓库实际出库数量；未发货为 null。与 `quantity` 不相等时表示少发或多发 |
| order_date | date | 下单时间 |
| promised_ship_date | date \| null | 商家或履约系统承诺的最晚发货日期；没有承诺日期时为 null，不能用预计送达日期替代 |
| shipped_date | date \| null | 发货时间，未发货为 null |
| delivered_date | date \| null | 签收时间，未签收为 null |
| delivery_recipient_type | enum \| null | 签收人类型：`Self` / `Other` / `Unknown`；未签收为 null。仅表示系统签收记录，不单独证明客户实际取得包裹 |
| estimated_delivery_date | date | 平台承诺的预计送达时间，用于计算是否延迟 |
| amount | number | 订单金额（元） |
| payment_status | enum | `Paid` / `Refunded` / `Pending` |

**实际出库字段约束**：

- `shipped_date` 为 null 时，`shipped_product_id` 与 `shipped_quantity` 必须均为 null
- `promised_ship_date` 只表示发货承诺，不等同于 `estimated_delivery_date`；缺失时不得推测具体发货日期
- `shipped_date > promised_ship_date` 表示未按承诺时间发货；是否催单仍需形成独立业务动作，不能仅靠客服话术声称已经执行
- `shipped_date` 不为 null 时，两个实际出库字段必须有值
- `shipped_product_id` 必须关联 Products 表中的有效商品
- `shipped_quantity` 必须为大于等于 0 的整数；允许大于 `quantity`，以支持多发场景
- 错发/少发判断来自订单记录与仓库出库记录的对比，不依赖客户对话中的单方陈述
- `delivered_date` 为 null 时，`delivery_recipient_type` 必须为 null；已签收但无法取得签收人类型时使用 `Unknown`
- `delivery_recipient_type = Self` 与客户“未收到货”的主张构成需要复核的冲突信号，但不能直接用于认定欺诈

**派生状态计算逻辑**（不作为存储字段，由 Order Agent 在查询时计算）：

```
若 delivered_date 为 null 且 当前日期 > estimated_delivery_date
    → 判定为"运输中延迟"
若 delivered_date 不为 null 且 delivered_date > estimated_delivery_date
    → 判定为"已签收但曾延迟"，延迟天数 = delivered_date - estimated_delivery_date
若 delivered_date 不为 null 且 delivered_date <= estimated_delivery_date
    → 判定为"正常签收"
```

这套计算逻辑本身就是 Order Agent 输出证据时置信度的来源之一：延迟天数越长、越接近政策条款的时效边界，证据置信度的设计空间越大，具体置信度打分规则见 `docs/05_DecisionRules.md`。

### Logistics（物流异常表）

> 与 Orders 表分工：Orders 表记录的时间戳能推算出"是否延迟"，但推算不出"为什么延迟"。真实物流异常（滞留、丢件、破损）往往有其独立的事件记录，因此保留一张独立的 Logistics 表，只记录**确实发生了异常事件**的订单，正常签收的订单不需要在此表中出现记录。

| 字段 | 类型 | 说明 |
|---|---|---|
| order_id | string | 关联 Orders 表 |
| carrier | string | 物流公司 |
| exception_type | string | 物流轨迹可观测异常，如 `"物流停滞超72小时"` `"滞留超72小时"` `"派送延迟"` `"路径异常"`；系统不得从长期未更新推导“疑似丢件”，商品破损和未收到货由客户另行申报 |
| exception_recorded_date | date | 异常事件被物流系统记录的时间 |
| resolved | boolean | 该异常是否已被物流方处理解决 |

**判断口径**：若某 `order_id` 未出现在本表中，视为"物流无异常记录"；若出现，Logistics Agent 直接引用该异常记录作为证据，不需要再从 Orders 表的时间戳去反推。

### Fulfillment Requests（履约操作请求表）

> 记录催发货等已经实际提交的履约业务动作。客服只有在本表成功创建记录后，才能向客户表示“已提交催单”；对话中的意愿确认不能替代业务记录。

| 字段 | 类型 | 说明 |
|---|---|---|
| fulfillment_request_id | string | 履约请求唯一标识，如 `FR2026080001` |
| order_id | string | 关联 Orders 表 |
| customer_id | string | 关联 Customers 表 |
| request_type | enum | 当前支持 `ExpediteShipment` |
| created_date | date | 请求实际创建日期 |
| workflow_status | enum | `Submitted` / `InProgress` / `Completed` / `Failed` |
| result | string \| null | 履约方返回结果，尚未完成时为 null |

**幂等约束**：同一订单存在 `Submitted` 或 `InProgress` 的 `ExpediteShipment` 时，不创建重复记录，直接返回原催单编号。

### Service Requests（客户业务申请表）

> **建模动机**：此前"客户提出退款/换货等诉求"这个动作只隐含在对话文本里，没有独立的数据实体记录申请本身的生命周期。把客户诉求单独建模为 Service Requests 后，**Decision Engine 处理的对象是"一条 Service Request"，而不是订单本身或一段对话**——Orders / Logistics / Products 是 Decision Engine 需要查询的证据来源，但裁决对象、状态流转、最终置信度都落在 Service Request 上。这也让 `docs/07_Evaluation.md` 阶段统计 Decision Accuracy、Human Escalation Rate 等指标时可以直接查表统计，而不必逐条重新解析对话文本。
>
> 命名从最初的 `Refund Requests` 改为 `Service Requests`：这张表包含退款、换货、部分退款多种申请类型（见下方 `request_type` 字段），沿用"Refund"命名容易让人误以为它只处理退款场景。

| 字段 | 类型 | 说明 |
|---|---|---|
| request_id | string | 申请唯一标识 |
| order_id | string | 关联 Orders 表 |
| customer_id | string | 关联 Customers 表 |
| request_type | enum | `Refund`（退款）/ `Exchange`（换货）/ `PartialRefund`（部分退款） |
| request_reason | string | 申请理由分类，如 `"商品损坏"` `"未收到货"` `"质量问题"` `"错发商品"` `"不想要了"` |
| requested_quantity | integer \| null | 本次申请涉及的商品数量；咨询类请求可为 null，其他请求需 ≤ Orders 表中的 `quantity` |
| request_date | date | 申请提交时间 |
| workflow_status | enum | 流程状态：`Pending` / `NeedsReflection` / `EscalatedToHuman` / `ExecutionPending` / `ExecutionFailed` / `ReturnAwaitingShipment` / `ReturnInTransit` / `ReturnAwaitingInspection` / `Closed` |
| decision | enum \| null | 业务结论：`Approve` / `Reject` / `PartialApprove` / `NoActionRequired`；未完成决策时为 null |
| approved_quantity | integer \| null | 实际批准的商品数量；仅在产生批准结果后填写 |
| approved_amount | number \| null | 实际批准金额（元）；仅在产生退款金额后填写 |
| decision_confidence | number \| null | Decision Engine 给出的最终置信度，未处理完成为 null |

**字段语义约束**：

- `workflow_status` 只表示客服决策工作流进度，不表示退款是否到账
- `decision` 与流程状态相互独立；例如已作出批准决定但外部履约尚未完成时，表示为 `workflow_status = ExecutionPending`、`decision = Approve`
- `ReturnAwaitingShipment` / `ReturnInTransit` / `ReturnAwaitingInspection` 只出现在"批准 + 无理由退货"场景：客户需要先寄回商品、客服验货后才真正退款，验货通过后转入 `ExecutionPending`，验货不通过直接转入 `Closed`（详见 `docs/06_LowLevelDesign.md` 5.5 节）
- `PartialRefund` 是客户申请类型，`PartialApprove` 是系统只批准部分申请，二者不可混用
- 使用 `PartialApprove` 时必须填写 `approved_quantity` 或 `approved_amount`
- 物流咨询等不产生批准/拒绝结论的请求使用 `NoActionRequired`

**与其他表的关系**：Service Requests 是 Decision Engine 的直接输入 / 输出对象——Planner Agent 接到客户请求后创建一条 Service Requests 记录，Order / Logistics / Policy Agent 围绕该记录中的 `order_id` 收集证据，Decision Engine 最终把裁决结果写回该记录的 `workflow_status`、`decision` 与 `decision_confidence` 字段。评估用例和可选语言输入均以 `request_id` 为关联锚点，而不是直接把一段文本绑定到 `order_id`。

### Service Request Evidence（申请证据表）

> **建模动机**：`request_reason` 与对话内容只代表客户提出的主张，不能自动视为已验证事实。质量问题、外观损坏等请求需要单独记录客户提交的图片、视频或检测报告，以及企业系统对材料的审核结果。该表保存结构化的证据元数据和审核结果；当前版本不存储真实媒体文件，也不声称由大语言模型完成真实性鉴定。

| 字段 | 类型 | 说明 |
|---|---|---|
| evidence_id | string | 证据唯一标识 |
| request_id | string | 关联 Service Requests 表；一条申请可对应多条证据 |
| evidence_type | enum | `Image` / `Video` / `DiagnosticReport` / `WarehouseRecord` / `LogisticsRecord` / `ChatStatement` |
| submitted_by | enum | `Customer` / `Warehouse` / `LogisticsProvider` / `AfterSalesCenter` / `System` |
| submitted_date | date | 证据提交或进入系统的日期 |
| original_filename | string | 客户上传时的原始文件名，仅作展示，不作为存储文件名 |
| media_type | enum | 当前支持 `image/jpeg` / `image/png` / `image/webp` |
| storage_path | string | 服务端生成的相对存储路径；不直接暴露为公共下载地址 |
| file_size_bytes | integer | 文件大小；当前单文件不得超过 10MB |
| description | string | 对材料内容的客观描述，不在此字段直接写最终责任结论 |
| verification_status | enum | `Pending` / `MachineReviewed` / `Verified` / `Rejected` / `NeedsHumanReview` |
| verified_facts | string[] | 审核过程中从材料中提取或确认的事实；为空表示尚未确认事实 |
| verification_confidence | number \| null | 材料审核置信度，范围 `0.0–1.0`；未审核时为 null |

**证据状态语义**：

- `Pending`：材料已提交但尚未审核，不能作为已确认事实；
- `MachineReviewed`：模型或规则完成初审，可按置信度参与决策，但不等同于专业鉴定；
- `Verified`：由企业系统、授权售后中心或其他可信来源确认，可作为高置信度证据；
- `Rejected`：材料无关、不可读或不能证明本次申请，不作为支持证据；
- `NeedsHumanReview`：机器无法可靠判断，必须进入人工审核流程。

**数据约束**：

- `request_id` 必须存在于 Service Requests 表；
- `submitted_date` 不得早于对应 Service Request 的 `request_date`；
- `verification_confidence` 若不为 null，必须处于 `0.0–1.0`；
- `Pending` 状态的 `verification_confidence` 应为 null，`verified_facts` 应为空数组；
- 客户提交的图片或视频只能证明材料中可观察到的现象，不能仅凭模型推断损坏责任、真实性或故障原因；
- 上传接口必须校验申请归属、文件大小和真实文件签名，不能只信任扩展名或客户端声明的 MIME；
- Logistics 和 Orders 已经保存的系统事实无需为了凑数量重复写入本表。

### 2.2.1 数据实体关系图（ER Diagram）

```
Customers
    │ customer_id
    ▼
  Orders ───────────────┐
    │ product_id         │ order_id
    ▼                    ▼
 Products            Logistics
                          ▲
                          │ order_id
                          │
                 Service Requests ◄──── customer_id（同时关联 Customers）
                    ▲             ▲
                    │ request_id  │ request_id
                    │             │
          Evaluation Input   Service Request Evidence
```

**五层职责分工**：

| 层 | 存的是什么 | 说明 |
|---|---|---|
| Orders / Logistics / Products | 企业事实数据 | 客观发生的交易、仓库出库和物流信息，不含任何客户诉求 |
| Service Requests | 业务事件 | 客户对某个订单提出的诉求（退款/换货/物流咨询等）；`workflow_status` 记录流程生命周期，`decision` 独立记录业务结论 |
| Service Request Evidence | 申请证据 | 保存客户材料、企业证明及其审核状态，将“客户主张”与“已验证事实”分开 |
| Evaluation Input | 客户请求文本 | Service Request 的语言表达；其中的陈述默认属于客户主张，不自动等同于已验证事实 |
| Decision Engine | 处理的对象 | **只处理 Service Request，不直接处理订单**——一个订单可能产生多条独立的 Service Request（如先申请换货，换货商品又出问题再申请退款），若 Decision Engine 直接绑定订单，就无法区分这是第几次诉求、也无法把"历史诉求记录"作为下一次裁决的证据之一 |

### Customers（客户表）

| 字段 | 类型 | 说明 |
|---|---|---|
| customer_id | string | 客户唯一标识 |
| vip_level | enum | `Normal` / `Silver` / `Gold` / `Platinum` |
| register_date | date | 注册日期 |
| refund_history_count | number | 历史退款成功次数，用于疑似恶意退货 / 欺诈风险判断（替代原 `complaint_history_count`：退款记录是更直接的行为证据，投诉次数不必然代表存在滥用退款的行为，两者不应混为一谈） |

### Products（商品表）

| 字段 | 类型 | 说明 |
|---|---|---|
| product_id | string | 商品唯一标识 |
| category | string | 商品类目，如 `"数码"` `"服饰"` `"食品"` |
| price | number | 商品单价 |
| is_fragile | boolean | 是否易损商品，影响"商品损坏"类请求的可信度判断 |
| is_customized | boolean | 是否定制商品（如刻字、定制尺寸）。定制商品通常不适用"无理由退货"条款，Policy Agent 检索退款政策时需要优先判断此字段，避免误判 |

## 2.3 场景覆盖矩阵

Mock 数据需要覆盖以下组合，确保 Decision Engine 的各类分支都能被真实数据触发，而不是靠人为编造对话内容。

### 类别 A：基础时效 / 物流场景

| 场景 | Orders | Logistics | Policy 预期匹配 | 对应用例 |
|---|---|---|---|---|
| 正常已签收 + 无异常 + 在时效内 | 已签收，未超时效 | 无异常 | 可退款 | Case 1 |
| VIP + 物流丢件 | 已支付，未签收 | Lost | 可退款（VIP优先） | Case 2 |
| 已签收超7天 + 无异常 | 已签收，超时效 | 无异常 | 不可退款 | Case 3 |
| 已签收超时效 + 物流曾异常 | 已签收，超时效 | 曾有 Exception 记录 | 政策与物流证据冲突 | Case 4 |

### 类别 B：商品与订单本身的问题

| 场景 | 说明 | 预期决策方向 |
|---|---|---|
| 商品质量问题（非易损品） | 收货后功能故障，非破损类问题，需要与客户申报的可见损坏区分 | 批准退款/换货，但证据要求与可见损坏不同 |
| 错发商品 | Orders 中 `shipped_product_id` 与 `product_id` 不相等，仓库出库记录可独立验证客户陈述 | 批准换货，平台承担运费 |
| 少发/漏发 | Orders 中 `shipped_quantity` 小于 `quantity`，仓库出库记录可独立验证少发数量 | 批准补发或部分退款 |
| 部分退款请求 | 订单含多件商品，客户只对其中一件不满意 | 需要按商品单价计算部分退款金额，而非整单退款 |

### 类别 C：客户与行为风险场景

| 场景 | 说明 | 预期决策方向 |
|---|---|---|
| 疑似恶意退货（高频退货客户） | `refund_history_count` 高 + 历史退货率异常，本次证据本身正常 | 即使证据支持退款，仍应触发风险复核（Reflection Trigger③） |
| 签收冲突：申请退款但系统显示本人签收 | 签收人类型记录与客户“未收到货”的申诉矛盾 | 触发复核，不可直接采信客户单方陈述，也不可自动认定欺诈 |
| 大额退款（超阈值） | 订单金额远高于平台均值 | 无论置信度高低，强制触发人工复核（Reflection Trigger③） |
| 新注册账号首单即大额退款申请 | `register_date` 接近当前日期 + 大额订单 + 退款申请 | 触发欺诈风险复核 |

### 当前冲突与证据边界用例（Case 11–20）

| Case | request_id | 场景 | 需要验证的系统路径 |
|---:|---|---|---|
| 11 | `SR2026080011` | 系统记录本人签收，客户称未收到 | 签收冲突 → Reflection |
| 12 | `SR2026080012` | 客户称错发，出库商品与订单一致 | 客户主张与仓库记录冲突 → Reflection |
| 13 | `SR2026080013` | 客户称少发，出库数量与订单一致 | 客户主张与仓库记录冲突 → Reflection |
| 14 | `SR2026080014` | 质量问题但没有提交证据 | 请求补充材料，不能直接批准或拒绝 |
| 15 | `SR2026080015` | 质量证据仍为 `Pending` | 等待审核，不能使用未审核事实 |
| 16 | `SR2026080016` | 证据商品与订单不匹配，审核为 `Rejected` | 证据无效并请求补充材料 |
| 17 | `SR2026080017` | 定制商品在7天内申请无理由退款 | 按定制商品例外拒绝 |
| 18 | `SR2026080018` | 易损商品有可见裂纹，但无运输异常记录 | 只能确认现象，进入 Reflection |
| 19 | `SR2026080019` | 系统确认少发1件，客户申请退2件 | 只批准已确认缺少数量，`PartialApprove` |
| 20 | `SR2026080020` | 已超过预计送达日，但物流未确认丢件 | 逾期不等于丢件，进入 Reflection 核查 |
| 21 | `SR2026080021` | 已付款但仓库尚未发货，客户申请取消订单 | 出库状态不可比较，按未发货取消政策批准 |

表中的“系统路径”用于说明数据覆盖目标，不写入运行时 Service Request。正式 Ground Truth 后续存放在独立 Evaluation Cases 中。

### 范围边界：不在本系统覆盖范围内的场景

以下场景与 `docs/01_PRD.md` 第 3 章"范围边界"呼应，明确不在当前 Mock Database
与 Decision Engine 的覆盖范围内，因此 Schema 中也不包含仅服务于这些场景的字段
（如 `stock`、`is_promotion`、拼团状态）：

- 价保申请（价格保护）、满减/优惠券订单的分摊退款、拼团未成团——均属于促销
  与价格类业务，涉及在线支付与营销系统集成，不属于售后决策范围；
- 换货目标商品缺货、二次换货升级——属于库存与履约系统集成范畴；
- 跨境订单清关归因、礼物订单收货人与下单人分离——属于跨境物流与地址簿系统
  集成范畴。

类别 A-C（第 2.3 节）覆盖的是系统实际处理的核心场景，Mock Database 规模以
覆盖度而非数量为优先——当前 21 条 Service Request Ground Truth（见上表）
已覆盖类别 A-C 的全部组合与边界冲突场景。

## 2.4 数据文件组织

```
data/mock_db/
├── orders.json
├── logistics.json
├── customers.json
├── products.json
├── service_requests.json
└── service_request_evidence.json
```

---

## 2.5 裁决与执行生命周期表

- `decision_records`：不可变裁决快照，保存规则版本、命中规则和输入摘要哈希。
- `action_tasks`：退款、取消订单和换货等实际副作用任务；通过
  `idempotency_key` 防止重复执行，并记录重试次数、错误及外部交易号。
- `outbox_events`：与业务状态在同一事务内写入，供事件发布器可靠投递。
- `audit_logs`：保存 Staff 身份、角色、操作对象、结果和必要的结构化详情。

申请状态按照 `ExecutionPending → Closed` 或
`ExecutionPending → ExecutionFailed` 流转。批准结论不等于外部履约已经成功。
人工队列在 `service_requests` 中使用 `assigned_to`、`lease_expires_at` 和
`row_version` 实现领取租约与乐观并发控制。

## 2.6 订单项与售后申请项

`orders` 只保存客户、支付和物流生命周期等订单头信息。商品购买快照进入
`order_items`：`product_id`、`sku_id`、规格、成交单价、购买数量、优惠金额、
实付金额以及实际出库商品和数量。金额裁决使用订单项的 `unit_price` 快照，不能
使用商品当前售价。

`service_request_items` 明确关联本次售后的 `order_item_id`，并分别保存申请数量、
批准数量和批准金额。当前自动裁决版本要求一条申请关联一个订单项；多商品订单若
未指定商品会要求客户先选择 SKU，不会默认对整单发起售后。未来支持一次申请多个
订单项时，可以在每个申请项上独立裁决后再汇总订单级处理状态。

# 3. Knowledge Base（Policy Agent 数据源）

## 3.1 设计原则

- 采用 Markdown 文档存储；实现版本没有接入向量检索（ChromaDB）——Policy Agent
  按请求类型、原因和已收集事实做确定性候选政策匹配（见 `src/backend/agents/policy_agent.py`
  与声明式规则 `data/decision_rules.json`），不是靠语义相似度检索命中条款。
  这与 `docs/03_BenchmarkResearch.md` 第 2 章的判断一致：订单财务类标准化场景
  用确定性 Workflow 约束比让模型自由检索/判断更可控，Markdown 政策文档本质上是
  给人看的业务说明与给 `rule_hits` 溯源用的编号来源，不是运行时检索数据源。
- 每条政策条款需要有明确编号（如 `REFUND-008`），便于 Decision Engine 的 `rule_hits` 字段引用溯源
- 政策条款需要包含"例外情形"，否则无法覆盖 Case 4 这类冲突场景

## 3.2 文档结构

```
data/knowledge_base/
├── refund_policy.md      # 退款政策
├── shipping_policy.md    # 物流政策
├── vip_policy.md         # 会员政策
└── risk_policy.md        # 风险复核与人工升级政策
```

## 3.3 政策条款设计示例（refund_policy.md 节选）

```markdown
# 退款政策

## REFUND-001：七天无理由退货
签收后 7 天内、非定制且不影响二次销售的商品，支持无理由退货。

## REFUND-007：政策冲突处理原则
当超时效拒绝条款与物流异常、商品安全或风险规则冲突时，
Decision Engine 不得简单覆盖规则，应记录全部证据并进入 Reflection。

## REFUND-002：超过七天的非质量退款
签收超过 7 天且无物流异常记录的，不支持无理由退货。

## REFUND-008：客户申报商品破损
物流系统不预先记录商品或包装破损；客户发起申请并提交材料，材料通过审核且申请及时的，才支持退款或换货；
若只有客户单方陈述，应降低置信度或进入 Reflection。
```

> 完整政策存放在 `data/knowledge_base/`。政策条款的证据权重和置信度计算在 `docs/05_DecisionRules.md` 中细化，本文档只负责数据结构与政策文件组织方式。

---

# 4. Evaluation Cases

## 4.1 核心评估用例

核心评估以自建业务 Case 为主。每条用例必须以 `request_id` 关联一条 Service Request，并由该请求进一步关联订单、客户、商品、物流异常和申请证据。预期结果来自明确的 Mock 事实与政策规则，不从对话文本主观推断。

建议的最小评估字段：

| 字段 | 说明 | 示例 |
|---|---|---|
| evaluation_id | 评估用例唯一标识 | `EVAL0001` |
| request_id | 关联 Service Requests | `SR2026080001` |
| user_input | 本轮客户输入 | `收到时商品已经损坏，我要退款` |
| expected_route | 预期工作流路径 | `Decision` / `Reflection` / `HumanEscalation` |
| expected_decision | 预期业务结论；未决策路径可为 null | `Approve` / `Reject` / `PartialApprove` / null |
| expected_rule_hits | 应命中的政策编号 | `["REFUND-008"]` |
| rationale | Ground Truth 的事实与规则依据 | `客户商品损坏材料已通过审核` |

Ground Truth 应单独存放在评估文件中，不写入客户输入，也不预先写入尚未执行的 Service Request。

## 4.2 场景化语言变体

同一 `request_id` 可以配置多种用户表达，例如简短陈述、口语化表达、缺少关键信息、情绪化表达或多轮补充。它们用于测试 Request Resolver 的识别和澄清能力，但共享同一组企业事实与业务 Ground Truth。

客户在输入中声称“少发”“错发”或“已损坏”只构成客户主张；是否成立仍由仓库出库记录、物流异常和 Service Request Evidence 判定。

## 4.3 数据文件组织

```text
data/
├── mock_db/                  # 核心企业事实与 Service Requests
├── knowledge_base/           # 核心政策规则
├── business_rules.json       # 阈值、金额、次数等环境参数
├── decision_rules.json       # 声明式裁决规则（priority 排序、when/decision/confidence）
└── evaluation/                # 评估 Ground Truth：single_turn/multi_turn/action_cases + llm_judge_cases
```

---

# 5. 数据准备工作流程

```
Step 1：设计业务场景矩阵与预期决策路径
        │
        ▼
Step 2：构造 Enterprise Mock Database，并确保外键与事实一致
        │
        ▼
Step 3：编写 Knowledge Base 政策文档，并为每条预期结论提供规则依据
        │
        ▼
Step 4：建立以 request_id 关联的 Evaluation Cases
        │
        ▼
Step 5：为核心 Case 编写多种用户输入和多轮澄清变体
        │
        ▼
Step 6：校验请求、企业事实、证据、政策与 Ground Truth 是否逻辑一致
```

---
