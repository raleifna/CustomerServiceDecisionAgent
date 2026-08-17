# 风险复核与人工升级政策

> 版本：v0.2（Demo 草案）  
> 适用范围：所有退款、换货、部分退款及高风险服务请求。  
> Demo 判定日期：由 Evaluation 运行参数提供，不在政策中固定写死。

## RISK-001：高频退款客户复核

### 触发条件

- `refund_history_count >= 10`。

### 处理要求

- 即使订单和商品证据支持退款，也不得直接关闭请求；
- 设置 `workflow_status = NeedsReflection`；
- 初次决策阶段保持 `decision = null`；
- Reflection 核查历史请求是否集中在短时间、是否存在重复理由，以及本次是否有独立事实证据。

### 说明

当前 Mock Schema 只记录退款成功次数，尚不能计算退款率。本条采用次数阈值作为 Demo 规则；未来增加 `completed_order_count` 后，应改用退款率与次数的组合判断。

## RISK-002：大额申请强制人工升级

### 触发条件

- 订单 `amount >= 5000.00`；
- 请求可能产生退款、补偿或高价值换货。

### 处理要求

- 设置 `workflow_status = EscalatedToHuman`；
- 自动 Decision Engine 不得给出最终批准或拒绝，`decision = null`；
- 记录订单金额、申请数量、基础政策判断和证据摘要，交由人工审核。

### 优先级

本条优先于自动批准、VIP 加速和普通 Reflection 规则。

## RISK-003：新注册账号首单高风险退款

### 触发条件

- `request_date - register_date <= 30天`；
- `refund_history_count = 0`；
- 本次请求涉及大额订单或高价值商品。

### 处理要求

- 若同时命中 `RISK-002`：直接 `EscalatedToHuman`；
- 未达到大额阈值但存在其他可疑信号：`NeedsReflection`；
- 新账号本身不能作为拒绝退款的唯一理由。

## RISK-004：客户陈述与系统事实冲突

### 触发条件

以下任一情况成立：

- 客户声称错发，但 `shipped_product_id = product_id`；
- 客户声称少发，但 `shipped_quantity = quantity`；
- 客户声称未收到，但系统存在有效签收记录；
- 客户申请数量大于订单数量或系统记录的异常数量；
- 对话中的时间、商品或数量与 Mock DB 明显矛盾。

### 处理要求

- 设置 `workflow_status = NeedsReflection`；
- 不得直接采信任一单方信息；
- 输出冲突字段、双方证据来源和需要补充的材料。

### 冲突后的业务边界

- 系统出库记录支持客户主张时，按 `REFUND-004` 或 `REFUND-005` 处理；
- 系统出库记录与客户主张冲突时，不得直接自动批准，也不得仅凭冲突自动拒绝；
- 可请求开箱视频、包裹重量记录、拣货复核记录或签收凭证；
- 补充证据仍无法消除冲突时，转人工处理；
- “客户陈述与系统事实冲突”是复核信号，不等于已认定欺诈。

## RISK-005：风险规则的决策边界

- `NeedsReflection` 是工作流状态，不是最终业务结论；
- `EscalatedToHuman` 是流程去向，不等于 `Reject`；
- 风险信号用于控制自动决策权限，不用于自动认定客户欺诈；
- 人工完成审核后，最终 `decision` 仍应使用 `Approve`、`Reject`、`PartialApprove` 或 `NoActionRequired`。

## RISK-006：申请数量超过已确认异常数量

### 触发条件

- `requested_quantity` 不超过订单数量，因此请求本身格式有效；
- 但 `requested_quantity > quantity - shipped_quantity`，即客户申请数量超过仓库记录可确认的少发数量。

### 处理要求

- 不得按客户申请数量全额批准；
- 若仓库记录可信且无其他冲突，可对已确认缺少数量作出 `PartialApprove`；
- `approved_quantity = quantity - shipped_quantity`；
- `approved_amount = product.price × approved_quantity`；
- 若客户对仓库记录提出异议或存在其他证据冲突，先进入 `NeedsReflection`。
