# 最终发布候选验收记录

本文件只记录合成数据和验证状态，不记录真实客户、金额、备注或真实文件名。

发布版本：1.0.0rc1

## 验收项目

| 项目 | 状态 | 证据或说明 |
| --- | --- | --- |
| Windows onedir 构建 | PASS | scripts\build_windows.ps1 通过；dist\CustomerLedger 目录大小 29.79 MiB，包含 exe、模板、静态资源和迁移文件 |
| 发布包不依赖外部 Python | PASS | Windows Sandbox 干净会话中限制 PATH、清除 Python 环境变量后，候选包启动并完成流程 |
| 匿名端到端流程 | PASS | 真实候选程序本机 HTTP 流程：新增客户、发货、超额收款、账目、未分配预收、追加收款和汇总 |
| 三种 .xlsx 导出 | PASS | 真实候选程序返回三个有效 ZIP/XLSX 文件，未写入仓库 |
| WPS 实际打开 | PASS | 使用显式 WPS et.exe 打开三份真实导出工作簿；未修改 .xlsx 默认 Excel 关联 |
| 备份恢复 | PASS | 真实候选程序完成手动备份、增加合成记录、恢复并验证恢复前后的数据差异 |
| 重启后数据持久化 | PASS | 正常关闭候选程序后用同一用户数据目录重启，账目和汇总仍可读取 |
| 程序目录替换 | PASS | 复制候选程序到独立目录，在保留同一用户数据目录的情况下启动并读取原有合成账目 |
| 完全离线运行 | PASS | Windows Sandbox 使用 Networking=Disable；回环访问、记账和导出成功，外部网络请求被阻断 |
| 第二实例阻止 | PASS | 第二个相同候选程序被 Windows 命名互斥体拦截 |
| 持久安全锁 | PASS | 真实候选程序在保护标记存在时对写入和导出返回 503，移除临时标记后恢复 |
| 旧账 .xls Dry Run | PASS | 仅使用已授权的 2 个真实旧表做 Dry Run；源文件摘要未改变，未执行导入 |
| 用户可见英文清理 | PASS | 阶段二界面英文关键词和装饰性英文扫描无命中 |
| 全部 pytest | PASS | 84 passed |
| ruff | PASS | ruff check . 通过，输出 All checks passed! |
| 迁移重复执行和检查 | PASS | flask db upgrade 两次均成功，flask db check 成功 |
| Git 与发布包敏感文件检查 | PASS | 未发现已跟踪运行数据库、旧表、工作簿、日志、备份或发布运行产物 |

## 当前判定

**PASS**。代码、构建、干净 Windows、断网、本机合成流程和 WPS 打开均已通过，可以创建阶段五本地提交。

按要求只创建本地提交，不 push、不创建 PR、不创建 GitHub Release。
