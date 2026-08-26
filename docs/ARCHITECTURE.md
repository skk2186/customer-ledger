# 架构说明

```text
浏览器（127.0.0.1）
        |
Flask routes/templates —— customer_service —— SQLAlchemy models
                                      |
                              SQLite + Alembic
        |
                       calculation_service（唯一公式入口）
```

## 分层

- `__init__.py`：应用工厂、默认配置、扩展初始化和路由注册。
- `models.py`：持久化实体与字段约束，不保存应收等派生真值。
- `customer_service.py`：客户校验、生命周期、审计和删除保护；路由不直接复制业务规则。
- `calculation_service.py`：发货、收款、分配和客户余额的唯一计算入口。
- `validation.py`：名称、规范化名称和 Excel Sheet 名称规则。
- `migrations/`：受控的 Alembic 迁移，应用启动不会隐式 `create_all`。

默认 SQLite 数据库位于 `runtime_data/`，可用 `SQLALCHEMY_DATABASE_URI` 覆盖。应用只绑定 `127.0.0.1`，阶段一不依赖 CDN。

## 一致性

PaymentAllocation 创建必须经过服务层校验：付款与发货客户一致、付款和分配有效、累积分配不超过付款金额。数据库层提供非负检查和外键；跨行规则由受控服务事务保证。
