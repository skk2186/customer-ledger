# 数据字典

所有时间戳为 UTC aware datetime；金额以整数分保存；吨位和平方数以百分之一整数单位保存；派生金额不保存为用户可编辑真值。

| 表 | 字段 | 类型/单位 | 说明 |
|---|---|---|---|
| customer | id/name/normalized_name | integer/text/text | 名称同时是 Excel Sheet 名，1–31 字符；规范化名称唯一 |
| customer | notes/active | text/boolean | 备注；有效或归档 |
| shipment | customer_id/shipment_date | integer/date | 客户外键和发货日期 |
| shipment | total_amount_cents/freight_cents/unloading_fee_cents | integer 分 | 总货款、运费、卸车费 |
| shipment | returned_pallet_tonnage_hundredths/area_hundredths | integer 0.01 单位 | 退板吨位、平方数，不参与应收 |
| shipment | returned_pallet_amount_cents/issue_deduction_cents/rounding_cents | integer 分 | 退板金额、问题扣费、抹零 |
| shipment | description/active | text/boolean | 内部说明；有效或作废 |
| payment | customer_id/payment_date | integer/date | 客户外键和收款日期 |
| payment | amount_cents | integer 分 > 0 | 收款金额 |
| payment | payment_method/description | text/text | 受控付款方式和独立说明 |
| payment | active | boolean | 有效或作废 |
| payment_allocation | payment_id/shipment_id | integer/integer | 付款和发货外键 |
| payment_allocation | allocated_amount_cents/active | integer 分/boolean | 分配金额；有效或撤销 |
| audit_event | object_type/object_id/action | text/text/text | 被操作对象和动作 |
| audit_event | before_summary/after_summary | text/text | 必要的短 JSON 摘要，不含完整敏感说明 |
| import_record | source_name/source_key/source_hash | text/text/text | 后续旧表幂等导入预留键和指纹 |
| import_record | status/message | text/text | 导入状态和机器可读说明 |
| submission_record | token/operation | text/text | 用户写操作的持久化幂等令牌和操作 |
| submission_record | result_type/result_id | text/integer | 令牌对应的结果对象 |

有效合计只纳入 active=True 的账务记录。客户汇总的“实收款”是有效 Payment 总额，不是已分配金额；未分配部分单独显示为预收。
