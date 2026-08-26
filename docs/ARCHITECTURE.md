# 架构说明

```text
浏览器（127.0.0.1）
        |
Flask routes/templates —— bookkeeping_service —— models
        |                     |                  |
        |              calculation_service       SQLite + Alembic
        |                     |
 export_service —— openpyxl  xls parser —— legacy_import_service
```

## 分层

- `__init__.py`：应用工厂、扩展初始化、静态过滤器和路由注册。
- `models.py`：Customer、Shipment、Payment、PaymentAllocation、AuditEvent、ImportRecord、SubmissionRecord；不保存应收等派生真值。
- `validation.py`：客户名、日期、十进制金额/数量、付款方式和提交令牌校验。
- `customer_service.py`：客户生命周期和审计。
- `bookkeeping_service.py`：发货、收款、分配、编辑、作废、撤销、厂里零售和幂等事务；所有账务写操作从这里进入。
- `calculation_service.py`：唯一计算入口，提供单据计算、客户账目行、客户汇总和全表合计。
- `export_service.py`：只从计算服务和有效原始记录生成 `.xlsx`；负责固定列、日期/数字类型、预收独立行、工作簿顺序和页脚合计，不在模板中复制业务公式。
- `legacy_import_service.py`：用 `xlrd` 只读解析 `.xls`，按 Parse → Normalize → Dry Run → Mapping/Exceptions → Confirmation → Backup → Transaction → Reconciliation 管线工作；Dry Run 只写忽略目录报告，确认导入才写正式模型。
- `migrations/`：受控 Alembic 迁移，应用启动不调用 `create_all()`。

## 事务与幂等

每个账务写服务拥有一个数据库事务。`SubmissionRecord.token` 是持久化唯一键；成功事务记录操作和结果 ID，同一令牌重放时返回原结果。事务中任意校验、关系或数据库错误都会回滚。

## 计算与日期

汇总服务按有效 Shipment 的 `shipment_date` 和有效 Payment 的 `payment_date` 应用截至日期；分配只影响单据实收和预收展示，客户净余额使用有效 Payment 总额，因此分配前后不变。负余额统一在页面标注为“预收余额”。

应用和开发脚本只绑定 `127.0.0.1`，不依赖 CDN、联网字体或遥测。

## 导出边界

客户 Sheet 的名称直接来自已通过 Excel 名称规则的 `Customer.name`。客户明细只包含有效 Shipment；每个未分配 Payment 余款追加为“预收款”行，因此明细行与页脚可以保持同一口径，同时客户摘要仍使用有效 Payment 总额。所有金额和数量先保持整数单位，再以受控 `Decimal` 写入工作簿。

## 旧账导入边界

解析器不使用 `openpyxl` 读取 `.xls`。它扫描整个工作表，不因第一条“合计”而提前结束；A:M 为正式业务列，N 及以后只记录异常。Customer、Shipment、Payment 和 Allocation 只有在用户确认映射/候选后才可进入单一事务。事务前使用 SQLite Online Backup API，备份失败即阻止导入；事务中任一异常都会回滚全部写入。`ImportRecord` 以源哈希、工作表和原始行号组成稳定来源键，支持重跑幂等。
