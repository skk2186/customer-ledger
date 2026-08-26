# 测试计划：阶段一

## 自动化覆盖

- 应用工厂、首页、`/healthz` 和本机绑定配置。
- 空 SQLite 库迁移、重复迁移和迁移后的表存在性。
- 整数分的 0.01 元精确往返，不使用 float。
- 应收公式：10000 元总货款减 300 元运费、100 元卸车费、1200 元退板金额为 8400 元。
- 2.50 吨退板和 520.80 平方数不改变应收；负应收保留。
- PaymentAllocation 不超过 Payment 且不能跨客户。
- 客户重名、名称规范化、非法 Sheet 名、归档和恢复。

## 验收命令

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m flask --app "customer_ledger:create_app" db upgrade
```

另行以本地进程执行启动烟雾测试并正常终止，不留下后台进程；最后检查 Git 不包含数据库、Excel 或运行产物。
