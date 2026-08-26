# 架构说明

```text
浏览器（127.0.0.1）
        |
Flask routes/templates —— bookkeeping_service —— models
                              |                  |
                    calculation_service       SQLite + Alembic
```

## 分层

- `__init__.py`：应用工厂、扩展初始化、静态过滤器和路由注册。
- `models.py`：Customer、Shipment、Payment、PaymentAllocation、AuditEvent、ImportRecord、SubmissionRecord；不保存应收等派生真值。
- `validation.py`：客户名、日期、十进制金额/数量、付款方式和提交令牌校验。
- `customer_service.py`：客户生命周期和审计。
- `bookkeeping_service.py`：发货、收款、分配、编辑、作废、撤销、厂里零售和幂等事务；所有账务写操作从这里进入。
- `calculation_service.py`：唯一计算入口，提供单据计算、客户账目行、客户汇总和全表合计。
- `migrations/`：受控 Alembic 迁移，应用启动不调用 `create_all()`。

## 事务与幂等

每个账务写服务拥有一个数据库事务。`SubmissionRecord.token` 是持久化唯一键；成功事务记录操作和结果 ID，同一令牌重放时返回原结果。事务中任意校验、关系或数据库错误都会回滚。

## 计算与日期

汇总服务按有效 Shipment 的 `shipment_date` 和有效 Payment 的 `payment_date` 应用截至日期；分配只影响单据实收和预收展示，客户净余额使用有效 Payment 总额，因此分配前后不变。负余额统一在页面标注为“预收余额”。

应用和开发脚本只绑定 `127.0.0.1`，不依赖 CDN、联网字体或遥测。
