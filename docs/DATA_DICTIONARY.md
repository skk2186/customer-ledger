# 数据字典

所有时间戳为 UTC aware datetime；所有主键为整数；所有金额字段以整数分保存；吨位和平方数以百分之一单位保存。

| 表 | 字段 | 类型/单位 | 说明 |
|---|---|---|---|
| customer | id | integer | 主键 |
| customer | name | text | 展示名称，1–100 字符，不含 Excel 禁止字符 |
| customer | normalized_name | text | NFKC、去首尾空白、合并空白、casefold；唯一 |
| customer | notes | text | 备注，可空 |
| customer | active | boolean | True 为有效，False 为归档 |
| customer | created_at/updated_at | timestamp | 时间戳 |
| shipment | customer_id | integer | 客户外键 |
| shipment | shipment_date | date | 发货日期 |
| shipment | total_amount_cents | integer 分 | 总货款 |
| shipment | freight_cents | integer 分 | 运费 |
| shipment | unloading_fee_cents | integer 分 | 卸车费 |
| shipment | returned_pallet_tonnage_hundredths | integer 0.01 吨 | 退板吨位，不参与应收公式 |
| shipment | returned_pallet_amount_cents | integer 分 | 退板金额 |
| shipment | issue_deduction_cents | integer 分 | 问题扣费 |
| shipment | area_hundredths | integer 0.01 平方单位 | 平方数，不参与应收公式 |
| shipment | rounding_cents | integer 分 | 抹零 |
| shipment | description | text | 说明 |
| shipment | active | boolean | True 为有效，False 为作废 |
| payment | customer_id | integer | 客户外键 |
| payment | payment_date | date | 收款日期 |
| payment | amount_cents | integer 分 | 收款金额 |
| payment | payment_method | text | 付款方式 |
| payment | description | text | 说明 |
| payment | active | boolean | True 为有效，False 为作废 |
| payment_allocation | payment_id/shipment_id | integer | 付款和发货外键 |
| payment_allocation | allocated_amount_cents | integer 分 | 分配金额 |
| payment_allocation | active | boolean | True 为有效，False 为作废 |
| audit_event | object_type/object_id | text/text | 被操作对象 |
| audit_event | action | text | 动作 |
| audit_event | before_summary/after_summary | text | 简短 JSON 摘要，不放完整备注 |
| import_record | source_name/source_key | text | 后续旧表幂等导入键，联合唯一 |
| import_record | source_hash/status/message | text | 内容指纹、状态和机器可读说明 |
