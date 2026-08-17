# 退款与售后政策

> 版本：v0.2（Demo 草案）  
> 适用范围：已创建订单后的退款、换货和部分退款请求。  
> 说明：本文件定义业务资格与基础决策方向；物流事实、会员权益和风险升级分别以对应政策为准。

## REFUND-001：七天无理由退货

### 适用条件

- 订单已经签收；
- `request_date - delivered_date <= 7天`；
- `request_reason` 为“不想要了”或其他非质量原因；
- 商品 `is_customized = false`；
- 订单付款状态为 `Paid`。

### 决策方向

- 条件全部满足：`Approve`；
- 批准数量为 `requested_quantity`；
- 基础批准金额为 `product.price × requested_quantity`。

### 例外

- 定制商品不适用本条；
- 商品质量、错发、少发、运输破损或安全隐患应优先适用专项条款；
- 命中风险政策时，风险政策的复核或人工升级要求优先。

## REFUND-002：超过三十天的非质量退款

### 适用条件

- 订单已经签收；
- `request_date - delivered_date > 30天`；
- 申请属于无理由退款、“不想要了”或其他非质量原因；
- 没有物流异常、质量问题或安全问题证据。

### 决策方向

- `Reject`。

### 例外

- 若存在独立的物流异常记录，不直接拒绝，转入 `NeedsReflection`，结合 `SHIPPING-004` 复核异常与退款诉求之间的因果关系；
- 商品安全隐患不受常规时效限制。

## REFUND-003：商品质量问题退款或换货

### 适用条件

- `request_reason = 质量问题`；
- 商品已经签收；
- 请求在签收后30天内提交；
- 请求数量不超过订单数量；
- 存在与该 Service Request 关联且能够支持质量主张的证据。

### 最低证据要求

| 问题类型 | 最低材料要求 | 可确认的范围 |
|---|---|---|
| 外观破裂、变形或明显瑕疵 | 能识别商品并清晰展示问题部位的图片 | 只能确认可见现象，不能仅凭图片确认责任原因 |
| 无法开机、功能失效 | 展示商品、连接方式和完整操作过程的视频，或授权检测报告 | 可确认操作后未正常工作；是否属于质量责任仍以审核结果为准 |
| 性能不达标或间歇性故障 | 授权售后中心的 `DiagnosticReport` | 以报告中确认的检测事实为准 |
| 起火、漏电等安全问题 | 图片或视频，并立即转人工 | 自动系统不得独立作最终责任认定 |

仅有 `ChatStatement` 或对话中的“商品坏了”不满足自动批准的最低证据要求。

### 证据状态处理

- `Verified`：作为高置信度证据参与决策；
- `MachineReviewed`：结合 `verification_confidence` 和其他事实参与决策；
- `Pending`：暂不作最终决定，应要求等待审核或补充材料；
- `Rejected`：不作为支持质量主张的证据；
- `NeedsHumanReview`：设置 `workflow_status = EscalatedToHuman`，自动流程不得给出最终批准或拒绝。

### 决策方向

- `request_type = Exchange`：`Approve` 换货；
- `request_type = Refund`：`Approve` 退款；
- `request_type = PartialRefund`：对申请涉及的数量执行 `Approve`，不视为 `PartialApprove`。

### 金额规则

- 部分退款基础金额：`product.price × requested_quantity`；
- `PartialApprove` 仅用于系统批准数量或金额小于客户申请的情况，并必须填写 `approved_quantity` 或 `approved_amount`。

### 例外

- 没有达到最低要求的商品问题证据时，不得直接批准，应要求补充材料或进入 `NeedsReflection`；
- 命中高频退款、大额退款或新账号风险时，按风险政策处理。

## REFUND-004：仓库错发商品

### 适用条件

- 订单已经发货；
- `shipped_product_id != product_id`；
- `request_reason = 错发商品`。

### 决策方向

- 默认 `Approve` 换货；
- 平台承担退回错发商品及重新发货产生的合理运费。

### 证据说明

`product_id` 与 `shipped_product_id` 的差异属于系统订单记录与仓库出库记录之间的独立事实比对，不依赖客户单方陈述。

## REFUND-005：仓库少发或漏发

### 适用条件

- 订单已经发货；
- `shipped_quantity < quantity`；
- `request_reason = 少发商品`。

### 决策方向

- 客户选择部分退款且 `requested_quantity <= quantity - shipped_quantity`：`Approve`；
- 批准数量为 `requested_quantity`；
- 批准金额为 `product.price × approved_quantity`；
- 客户选择补发时，可批准补发缺少数量。

### 例外

- 客户申请数量超过系统记录的缺少数量时，不得直接全额批准，应进入 `NeedsReflection` 或作出 `PartialApprove`。

## REFUND-006：运输破损

### 适用条件

- Logistics 中存在 `exception_type = 运输破损`；
- 异常订单与 Service Request 的 `order_id` 一致；
- 客户申请退款或换货。

### 决策方向

- 异常尚未解决且申请及时：`Approve`；
- 退款金额按申请涉及商品数量计算；
- 平台承担合理退货运费。

### 例外

- 若只有客户陈述，没有物流异常或其他证明，应降低置信度并根据易损属性决定是否进入复核；
- 若物流记录与客户陈述明显矛盾，进入 `NeedsReflection`。

## REFUND-007：政策冲突处理原则

当同一请求同时命中批准与拒绝条款时，Decision Engine 不得通过简单覆盖规则直接给出结论。

### 触发条件

- 超过退款时效，但存在物流异常；
- 客户陈述与订单、仓库或物流记录冲突；
- 基础售后条款支持申请，但风险政策要求复核或人工升级。

### 决策方向

- 设置 `workflow_status = NeedsReflection`；
- `decision = null`，直到 Reflection 完成；
- 记录全部冲突条款和事实证据，不得只保留最终命中的一条。

### 优先级

人工升级规则 > 风险复核规则 > 安全例外 > 专项售后规则 > 常规时效规则。

## REFUND-008：客户报告破损但无物流异常

### 适用条件

- `request_reason = 商品损坏`；
- Logistics 没有对应的 `运输破损` 记录；
- 客户提交了能够识别订单商品并展示损坏现象的材料。

### 判断原则

- 无物流异常记录不等于已经证明商品没有损坏；
- 商品 `is_fragile = true` 只能作为辅助风险信息，不能单独证明运输责任；
- 图片可以支持“存在可见损坏”，但不能单独确认损坏时间、责任方或是否人为造成。

### 决策方向

- 材料为 `Verified` 且明确确认非人为质量问题：按 `REFUND-003` 处理；
- 材料为 `MachineReviewed` 且只能确认表面损坏：进入 `NeedsReflection`；
- 没有材料或材料未达到最低要求：请求补充材料，保持 `decision = null`；
- 材料为 `NeedsHumanReview`：转人工处理。

## REFUND-009：定制商品售后边界

### 适用条件

- 商品 `is_customized = true`。

### 决策方向

- 因“不想要了”等无理由原因申请退款：`Reject`；
- 存在质量问题、错发、少发、运输破损或安全问题时，不得仅因商品为定制商品而拒绝，应适用相应专项条款。

### 说明

定制属性只排除无理由退货资格，不排除法定或平台承诺的质量售后责任。

## REFUND-010：未发货订单取消

### 适用条件

- 订单付款状态为 `Paid`；
- `shipped_date = null`；
- `shipped_product_id = null`；
- `shipped_quantity = null`；
- 客户申请原因为“未发货取消订单”。

### 决策方向

- 条件全部满足：`Approve` 取消订单并原路退款；
- 批准数量为 `requested_quantity`；
- 基础批准金额为 `product.price × requested_quantity`。

### 例外

- 若订单在申请提交后已经进入实际出库，应重新获取最新订单状态，不得使用旧快照自动取消；
- 定制生产、预售锁单或其他不可取消履约阶段当前不在 Mock Schema 中表达，若未来增加相关字段，应优先检查这些限制；
- 命中大额或其他强制人工规则时，按风险政策处理。
