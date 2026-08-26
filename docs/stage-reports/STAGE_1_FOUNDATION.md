# 阶段一验收报告：基础数据模型与客户管理

## 结论

**PASS** — 阶段一验收项全部通过，可以创建本地阶段提交。报告只使用合成测试信息，不含真实客户、账务或表格数据。

## Git

- 当前分支：`develop`。
- 分支仅有：`main`、`develop`。
- `main` 已有空初始化提交；未创建其他分支、PR、远程修改或 push。
- 阶段提交尚未创建；待本报告确认后创建唯一阶段提交。

## 交付文件

- `AGENTS.md`、`README.md`。
- `docs/PRODUCT_SPEC.md`、`docs/ARCHITECTURE.md`、`docs/DATA_DICTIONARY.md`、`docs/EXCEL_CONTRACT.md`、`docs/TEST_PLAN.md`。
- `pyproject.toml`、`requirements-dev.txt`、`.gitignore`、`scripts/dev_start.ps1`。
- `src/customer_ledger/`：应用工厂、路由、模板、静态样式、校验、客户服务和统一计算服务。
- `migrations/`：`0001_foundation`、`0002_unique_normalized_name_index` 与 `0003_enforce_excel_safe_customer_name` 版本化迁移。
- `tests/`：19 个自动化测试。

## 模型

已实现并迁移：`Customer`、`Shipment`、`Payment`、`PaymentAllocation`、`AuditEvent`、`ImportRecord`。金额字段使用整数分，吨位和平方数使用百分之一整数单位；ORM 严格拒绝浮点和布尔值。应收、实收、欠款和净余额均由 `calculation_service.py` 计算，不作为用户可编辑真值保存。

## 已执行命令与结果

环境：Python 3.10.6、Git 2.40.0、干净 `.venv`；依赖按 `pyproject.toml` 安装。

```text
python -m venv .venv                         PASS
pip install -e ".[dev]"                      PASS
flask --app "customer_ledger:create_app" db upgrade（空库） PASS
同一 db upgrade 再次执行                    PASS
flask --app "customer_ledger:create_app" db check             PASS
python -m pytest                             19 passed
python -m ruff check .                       All checks passed
本地启动 smoke：/healthz 200、/ 200          PASS
服务进程关闭后不存在                         PASS
Git 产物审计：无跟踪 Excel/数据库/运行产物     PASS
```

覆盖内容包括 0.01 元精确往返、应收 8400 元、退板吨位/平方数不改变应收、负应收、分配上限、跨客户分配、名称规范化重名、非法 Sheet 名、归档恢复和账务历史删除保护。

## 外部审核修复

- 问题：客户名称原先允许最多 100 个字符，与未来 Excel Sheet 名的 31 字符规则冲突。
- 修复：新增唯一共享入口 `validate_excel_safe_name`，客户新增、修改和 Sheet 校验统一执行 1–31 字符、Excel 禁止字符、控制字符及首尾单引号规则；不截断、不自动改名。表单 `maxlength` 已改为 31，并明确提示名称会直接用作 Excel Sheet 名。
- 数据库：新增 `0003_enforce_excel_safe_customer_name`，将 `Customer.name` 改为 `VARCHAR(31)` 并增加长度 CHECK。迁移会先检测旧库超长/空名称，发现不合规即中止，不修改数据；`0001`、`0002` 未改动。
- Excel 契约：固定只导出 `.xlsx`、客户 Sheet 名与客户名称完全一致、首个 Sheet 为“客户汇总总表”、客户表 13 列顺序、末行为“合计”；本阶段未实现导出。
- 真实修复验收：`pytest` 19 passed；`ruff check .` 通过；空库升级、从 0002 旧库升级、重复升级和 `flask db check` 通过；超长旧数据迁移会安全阻断且原值不变；首页与 `/healthz` 启动烟雾测试及 Git 敏感文件检查通过。

## 风险与边界

- 阶段一没有发货/收款业务页面，也没有 Excel 适配器；跨客户和分配上限规则应继续通过服务层写入。
- 当前默认数据库是本机 SQLite，监听和启动脚本均限定 `127.0.0.1`；尚未做备份恢复或 Windows 打包。
- CSRF、用户认证和多用户并发控制不在本阶段范围，未来对外部署前必须补齐。

## 下一阶段条件

下一阶段开始前应保持迁移链可升级、继续只使用整数单位，并基于 `calculation_service.py` 实现发货/收款录入和分配页面；Excel 导入导出须遵守 `EXCEL_CONTRACT.md` 并使用 `ImportRecord` 幂等。
