# 客户快捷填表系统

这是一个本机优先的客户快捷填表和账目管理系统，当前包含 Windows 桌面发布候选版。

## 安装

需要 Python 3.10+ 和 Git。在 Windows PowerShell 执行：

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

## Windows 桌面版

发布包是 dist\CustomerLedger\ 目录，双击其中的 CustomerLedger.exe 即可使用，不需要安装 Python，也不使用 Flask 开发服务器。

桌面版只在本机 127.0.0.1 启动服务，账库和运行状态默认保存在 %LOCALAPPDATA%\CustomerLedger；备份默认保存在“文档\客户账本备份”，导出文件默认保存在“文档\客户账本导出”。导出设置可在页面中改到其他文件夹，设置保存在 %LOCALAPPDATA%\CustomerLedger\settings.json，不写入账库。替换程序目录前请先关闭程序，不要删除上述用户数据目录。

## 开发启动

```powershell
.\.venv\Scripts\python.exe -m flask --app "customer_ledger:create_app" db upgrade
.\.venv\Scripts\python.exe -m flask --app "customer_ledger:create_app" run --host 127.0.0.1 --port 5000
```

也可以运行 `powershell -ExecutionPolicy Bypass -File .\scripts\dev_start.ps1`。打开 <http://127.0.0.1:5000/>；健康检查地址为 `/healthz`。

## 测试与检查

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m flask --app "customer_ledger:create_app" db upgrade
.\.venv\Scripts\python.exe -m flask --app "customer_ledger:create_app" db check
```

## 当前能力

- 首页三个主要入口：新增一笔、客户账目、客户汇总总表；另有厂里零售和新增收款快捷入口。
- 发货录入：原始字段、初始收款、十进制安全解析、服务端重算应收/欠款和重复提交保护。
- 收款录入：银行转账、微信、支付宝、现金、其他；暂不分配、指定发货和按最早未结清自动分配。
- 客户账目：固定发货明细顺序、系统合计、收款、未分配预收、编辑、作废、取消收款与货款的对应关系和历史标记。
- 客户汇总总表：按本机截至日期筛选，固定列、客户合计、全表合计、归档客户标识和预收余额标识；页面只读。
- Excel 导出：当前客户、客户汇总总表和全部客户账目；客户 Sheet 名严格使用客户名称，未分配预收以独立明细行导出。导出文件使用中文名称并自动追加序号，不会静默覆盖；导出完成后显示保存位置。
- 旧账迁移：选择 `.xls` 后先做 Dry Run，检查客户映射、A:M 正式业务区间、异常和汇总参考，再经确认、SQLite 备份和单事务导入；重复来源按文件哈希、工作表和原始行号幂等。
- 厂里零售：复用普通 Customer、Shipment、Payment、PaymentAllocation 模型。
- 所有账务写操作通过 `bookkeeping_service.py` 事务服务并写入必要审计摘要。

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

开发模式的运行时数据库默认写入被 Git 忽略的 runtime_data/customer_ledger.db；导出、Dry Run 报告和备份也只写入被忽略目录。旧 .xls 原件应放在 private_samples/ 或用户明确指定的外部目录，绝不提交到 Git。Windows 构建说明见 WINDOWS_DEPLOYMENT.md，最终验收记录见 FINAL_ACCEPTANCE.md。
