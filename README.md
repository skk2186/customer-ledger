# 客户快捷填表系统

阶段一建立一个仅供本机使用的 Flask 基础工程、版本化数据库模型、统一计算服务和客户管理闭环，为后续发货、收款和旧表导入做准备。

## 安装

需要 Python 3.10+ 和 Git。在 Windows PowerShell 执行：

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

## 启动

```powershell
.\.venv\Scripts\python.exe -m flask --app "customer_ledger:create_app" db upgrade
.\.venv\Scripts\python.exe -m flask --app "customer_ledger:create_app" run --host 127.0.0.1 --port 5000
```

也可以运行 `powershell -ExecutionPolicy Bypass -File .\scripts\dev_start.ps1`。打开 <http://127.0.0.1:5000/>；健康检查地址为 `/healthz`。

## 测试与检查

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
```

## 当前能力

- 首页、健康检查和本机 Flask 启动。
- 客户列表、搜索、新增、修改名称/备注、归档和恢复。
- Customer、Shipment、Payment、PaymentAllocation、AuditEvent、ImportRecord 模型及初始迁移。
- 整数分金额、百分之一数量和唯一的统一计算服务。
- PaymentAllocation 的金额上限和客户一致性校验。

阶段一明确不包含发货/收款业务页面、汇总总表、Excel 导入导出、备份恢复和 Windows 打包。

## 目录

```text
AGENTS.md
README.md
docs/
migrations/
scripts/
src/customer_ledger/
tests/
pyproject.toml
```

运行时数据库默认写入被 Git 忽略的 `runtime_data/customer_ledger.db`。请先阅读 [AGENTS.md](AGENTS.md) 和 `docs/` 中的契约。
