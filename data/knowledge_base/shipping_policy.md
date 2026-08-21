# 物流与履约政策

> 版本：v0.3（Demo 草案）  
> 适用范围：订单发货承诺、已发货订单的运输时效、物流异常和未收到货请求。

## SHIPPING-001：订单物流状态判定

Order Agent 根据 Orders 中的时间字段计算状态，状态本身不重复存储。

- `delivered_date = null` 且当前日期未超过 `estimated_delivery_date`：运输中，未延迟；
- `delivered_date = null` 且当前日期超过 `estimated_delivery_date`：运输中延迟；
- `delivered_date <= estimated_delivery_date`：正常签收；
- `delivered_date > estimated_delivery_date`：延迟签收。

计算状态只能说明是否延迟，不能替代 Logistics 中记录的异常原因。

## SHIPPING-002：物流轨迹停滞

### 适用条件

- 订单已经发货但 `delivered_date = null`；
- Logistics 存在 `exception_type = 物流停滞超72小时`；
- `resolved = false`；
- 客户提出“未收到货”相关请求。

### 决策方向

- 物流查询：允许告知轨迹停滞并发起承运方催查；
- 轨迹停滞不能推导为疑似丢件，更不能直接证明包裹已经丢失；
- 退款请求：保持 `decision = null`，等待承运商核查结果；
- 若同时命中会员政策，可按会员优先规则加速处理。

### 例外

- 客户实际取件时发现没有包裹，可主动上报“未收到货”，平台创建核查任务；
- 异常已解决或包裹恢复运输时，应重新获取最新物流事实；
- 物流记录显示本人签收而客户声称未收到时，不直接批准，进入 `NeedsReflection`。

## SHIPPING-004：滞留超过72小时

### 适用条件

- Logistics 存在 `exception_type = 滞留超72小时`；
- 异常发生在订单运输过程中。

### 决策方向

- 未签收且异常未解决：支持催办、退款或补救处理；
- 已签收且仍在常规售后时效内：物流异常可作为支持客户申请的补充证据；
- 已签收超过7天：不能仅凭历史滞留记录直接批准退款，应进入 `NeedsReflection`。

### Reflection 核查项

- 异常是否实际影响商品状态或客户使用；
- 异常是否已经解决；
- 客户申请原因与物流异常是否存在因果关系；
- 是否同时命中 `REFUND-002` 的超时拒绝条件。

## SHIPPING-005：无物流异常记录

若某订单未出现在 `logistics.json` 中，表示企业物流系统没有记录独立异常事件。

### 判断原则

- 不得把“无异常记录”解释为客户陈述必然虚假；
- 可结合 Orders 时间戳判断是否存在可计算的延迟；
- 客户声称丢件或破损，但系统无对应记录时，应降低物流证据置信度，必要时进入 Reflection。

## SHIPPING-006：已签收但客户声称未收到

### 适用条件

- Orders 中存在 `delivered_date`；
- 客户提出“未收到货”相关请求。

### 判断原则

- 签收记录属于系统事实，但“已签收”不必然等于客户本人实际取得包裹；
- `delivery_recipient_type = Self` 表示系统记录为本人签收，与客户主张构成高强度冲突；
- `delivery_recipient_type = Other` 或 `Unknown` 时，应核查代收人、签收凭证和配送地址，不能推定客户虚假陈述。

### 决策方向

- 不得仅凭客户陈述自动批准退款；
- 进入 `NeedsReflection`，保持 `decision = null`；
- Reflection 应请求承运方签收凭证，并核对签收人类型、时间和地址；
- 关键冲突无法消除时转人工，不得自动认定客户欺诈。

## SHIPPING-007：配送逾期但尚未确认丢件

### 适用条件

- `delivered_date = null`；
- 当前日期或 `request_date` 已超过 `estimated_delivery_date`；
- Logistics 中没有未解决的物流停滞或其他可解释异常记录。

### 决策方向

- 物流查询：告知已经逾期并发起承运方核查；
- 退款请求：不得把“逾期”直接等同于“丢件”，进入 `NeedsReflection`；
- 核查确认丢件后按 `SHIPPING-002` 处理；包裹仍在运输且可恢复时，按最新物流结果决定催办、继续等待或其他补救方案。

## SHIPPING-008：未发货订单与发货承诺

### 适用条件

- `shipped_date = null`；
- 客户询问是否发货或预计发货时间。

### 回复原则

- `promised_ship_date` 有值：可以告知商家或履约系统承诺的最晚发货日期；
- `promised_ship_date` 为 null：只能告知尚未发货，不得根据 `estimated_delivery_date` 反推出具体发货日期；
- `estimated_delivery_date` 是预计送达日期，不等同于发货承诺；
- 可以询问客户是否需要催发货；客户明确确认后，系统可创建 `ExpediteShipment` 履约请求；
- 只有 `fulfillment_requests.json` 成功创建 `Submitted` 记录后，才能告知“已提交催单”；
- 同一订单已有 `Submitted` 或 `InProgress` 的催单时应返回原催单编号，不得重复创建；
- 创建失败时只能告知暂未提交并建议人工处理，不得声称“已经催促”。
