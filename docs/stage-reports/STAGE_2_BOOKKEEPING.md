# 阶段二报告：普通会计用户记账闭环

## 结论

PASS（本地验收完成）。本阶段在 `develop` 上完成普通会计用户的发货、收款、预收款、分配、编辑、作废、撤销和汇总查看闭环；未实现 Excel 导入导出、备份恢复或 Windows 打包。

## 交付内容

- `src/customer_ledger/bookkeeping_service.py`：统一的发货、收款、分配、编辑、作废、撤销和厂里零售服务层；写操作使用数据库事务，并带幂等令牌和审计事件。
- `src/customer_ledger/calculation_service.py`：统一应收、本单实收、本单欠款、客户总实收、客户净余额和未分配预收计算。
- `src/customer_ledger/routes.py` 及 `templates/`：新增一笔、厂里零售、客户账页、客户汇总总表及相关操作页面。
- `migrations/versions/0004_bookkeeping_workflow.py`、`0005_align_submission_token_index.py`：收款金额改为严格大于零，并增加幂等提交记录表；未修改已有迁移。
- `tests/test_bookkeeping.py`：金额精度、应收公式、分配约束、自动分配、编辑与作废、幂等、零售映射、汇总和 HTTP 流程测试。
- `README.md`、`docs/PRODUCT_SPEC.md`、`docs/ARCHITECTURE.md`、`docs/DATA_DICTIONARY.md`、`docs/TEST_PLAN.md`：补充阶段二能力、边界和运行说明。

## 实际验收命令与结果

在项目虚拟环境（Python 3.10.6）中执行：

- `.\\.venv\\Scripts\\python.exe -m pytest`：PASS，31 passed。
- `.\\.venv\\Scripts\\python.exe -m ruff check .`：PASS。
- 空 SQLite 数据库执行 `flask db upgrade`：PASS。
- 同一数据库重复执行 `flask db upgrade`：PASS。
- `flask db check`：PASS。
- 实际启动 `127.0.0.1` Flask 服务并访问首页、`/healthz`、客户新增、发货、两次收款、客户账页、`/summary` 和作废流程：PASS，全部 HTTP 200；服务已正常关闭，未留下后台进程。
- Git 敏感文件检查：PASS；未跟踪或提交真实表格、数据库和运行产物。

烟雾测试使用临时数据库和合成客户名，不含真实业务数据。

## 业务规则确认

- 金额以整数分保存，数量以百分之一单位保存；输入解析不使用 float。
- 应收款 = 总货款 - 运费 - 卸车费 - 退板金额 - 问题扣费；退板吨位和平方数不参与应收计算；负应收不截断。
- 有效分配不得超过收款，也不得跨客户；未分配部分作为客户预收款展示。
- 作废为可追溯状态变更，不物理删除；作废发货会使其有效分配失效，收款仍可形成预收款；作废收款会使其分配失效。
- 账页和汇总表为只读展示，写操作集中在服务层并记录 `AuditEvent`。

## 风险与下一阶段条件

- 当前尚未实现 Excel 导入导出，因此固定 Excel 契约尚未由代码生成文件验证。
- 当前范围未包含权限、并发冲突解决和备份恢复；生产化前需补充授权、并发策略、备份策略和更完整的数据库约束测试。
- 下一阶段可在本阶段模型和计算服务稳定的前提下实现 Excel 导出，再依据 `docs/EXCEL_CONTRACT.md` 增加文件级验收测试。
