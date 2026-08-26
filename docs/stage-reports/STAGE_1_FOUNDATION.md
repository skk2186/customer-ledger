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
- `migrations/`：`0001_foundation` 与 `0002_unique_normalized_name_index` 版本化迁移。
- `tests/`：15 个自动化测试。

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
python -m pytest                             15 passed
python -m ruff check .                       All checks passed
本地启动 smoke：/healthz 200、/ 200          PASS
服务进程关闭后不存在                         PASS
Git 产物审计：无跟踪 Excel/数据库/运行产物     PASS
```

覆盖内容包括 0.01 元精确往返、应收 8400 元、退板吨位/平方数不改变应收、负应收、分配上限、跨客户分配、名称规范化重名、非法 Sheet 名、归档恢复和账务历史删除保护。

## 风险与边界

- 阶段一没有发货/收款业务页面，也没有 Excel 适配器；跨客户和分配上限规则应继续通过服务层写入。
- 当前默认数据库是本机 SQLite，监听和启动脚本均限定 `127.0.0.1`；尚未做备份恢复或 Windows 打包。
- CSRF、用户认证和多用户并发控制不在本阶段范围，未来对外部署前必须补齐。

## 下一阶段条件

下一阶段开始前应保持迁移链可升级、继续只使用整数单位，并基于 `calculation_service.py` 实现发货/收款录入和分配页面；Excel 导入导出须遵守 `EXCEL_CONTRACT.md` 并使用 `ImportRecord` 幂等。
