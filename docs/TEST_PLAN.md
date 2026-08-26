# 测试计划：阶段三

## 自动化覆盖

- 阶段一 19 个测试继续通过。
- Decimal 安全解析、空数字、两位小数、非法文本、科学计数法、NaN、Infinity 和负数拒绝。
- 发货应收 8400 元、初始实收 8000 元、欠款 400 元；退板吨位/平方数不影响应收；负应收和抹零。
- 一单多次收款、一笔收款分配到多笔发货、部分分配、先预收后发货、自动分配和超额预收。
- 跨客户/超额分配拒绝、净余额在分配前后不变、事务失败回滚、重复提交幂等。
- 发货编辑、客户关系保护、发货/Payment 作废、分配撤销和审计关系。
- 厂里零售映射、已收/未收以及不创建 0 元 Payment。
- 多客户截至日期汇总、归档客户、固定合计行、全表恒等式和只读 POST 405。
- 空库升级、阶段一数据库升级、重复升级和 `flask db check`。
- `.xlsx` 客户明细、预收独立行、客户汇总、全部账目首个 Sheet、固定表头、Excel 日期/数字类型、Decimal 读回、无公式/外部链接/宏和页脚恒等式。
- 匿名 `.xls` 生成与读取、标题/表头/日期/空行/合计后续行/备注/预收/未识别行分类、A:M 正式区与 N 列异常、客户冲突映射和历史汇总仅参考。
- Dry Run 零业务写入、确认映射、付款分配和未分配预收、备份失败阻断、后续异常全量回滚、同源幂等、哈希变化不误去重、未确认预收不落库。
- 导出和迁移页面的用户可见文本为中文，普通用户不接触 Python、SQL 或 ORM 术语。

## 验收命令

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m flask --app "customer_ledger:create_app" db upgrade
.\.venv\Scripts\python.exe -m flask --app "customer_ledger:create_app" db check
```

另行以真实本地进程完成匿名合成流程：新增客户 → 发货 → 超额初始收款 → 客户账目 → 未分配预收 → 再次收款 → 汇总 → 导出；再在旧账迁移页执行一次 `.xls` Dry Run。服务正常关闭且不留下进程。最后检查 Git 不包含数据库、Excel、日志、导出、备份或真实数据，并对真实旧表 Dry Run 前后复核 SHA-256 不变。
