# 客户快捷填表系统协作约定

## 目录与职责

- `src/customer_ledger/`：Flask 应用、模型、校验、服务层和模板。
- `migrations/`：Alembic/Flask-Migrate 版本化数据库迁移；生产环境禁止使用 `create_all` 代替迁移。
- `tests/`：自动化测试。
- `docs/`：产品、架构、数据字典、Excel 契约和测试计划。
- `runtime_data/`、`private_samples/`、`exports/`、`backups/`、`logs/`：本地运行或敏感数据目录，禁止进入 Git。
- 客户名称就是未来的 Excel Sheet 名：去除首尾空白后必须为 1–31 个字符，不能含 Excel 禁止字符、控制字符或首尾单引号；不截断、不自动改名。

## 运行与测试

在 Windows PowerShell 中：

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m flask --app "customer_ledger:create_app" db upgrade
.\.venv\Scripts\python.exe -m flask --app "customer_ledger:create_app" run --host 127.0.0.1 --port 5000
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
```

开发启动脚本为 `scripts\dev_start.ps1`。数据库只通过版本化迁移升级，重复执行迁移必须安全幂等。

## 业务公式

- 应收款 = 总货款 - 运费 - 卸车费 - 退板金额 - 问题扣费。
- 本单实收 = 有效 PaymentAllocation 求和。
- 本单欠款 = 应收款 - 本单实收 - 抹零。
- 客户总实收 = 有效 Payment 求和。
- 客户净余额 = 有效发货应收总额 - 有效收款总额 - 有效抹零总额。

金额统一使用整数分，数量统一使用百分之一单位；派生金额必须通过 `calculation_service.py` 计算，不持久化为可编辑真值。

## 隐私边界

本项目默认只监听 `127.0.0.1`，不加载 CDN。真实客户、真实账务、真实 Excel、导出文件、备份、运行数据库和日志不得提交到 Git。测试只能使用合成数据；审计摘要不得记录完整敏感备注。

## Git 规则

- 只允许 `main` 和 `develop` 两个分支，不创建其他分支，不创建 PR。
- 新仓库先初始化 `main` 并创建空初始化提交，再创建并切换到 `develop`。
- 发现来源不明的分支或修改时停止，不清理、不覆盖。
- 阶段全部验收通过后，最多创建一个本地提交，提交信息必须为 `stage 1: foundation data model and customers`。
- 禁止 push，禁止修改远程仓库。

## 阶段报告

每阶段在 `docs/stage-reports/` 生成报告，写明 `PASS` 或 `BLOCKED`、文件、模型、命令、测试、风险和下一阶段条件。报告不得包含真实数据。只有全部验收通过才创建阶段提交；失败时不提交。
