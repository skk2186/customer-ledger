# Windows 桌面版部署说明

## 发布包

发布候选采用 PyInstaller onedir 目录，入口为：

    dist\CustomerLedger\CustomerLedger.exe

整个 CustomerLedger 目录必须一起分发，不能只复制 exe。发布包内包含应用模板、静态资源和版本化迁移文件，不包含账库、备份、导出文件、日志或旧账原件。

最终用户双击 CustomerLedger.exe 即可启动。程序使用 Waitress 提供本机 WSGI 服务，再由 pywebview 打开窗口；服务只监听 127.0.0.1，不向局域网或互联网提供账务页面，不加载 CDN。

## 用户数据与更新

桌面版默认使用以下目录：

| 内容 | 默认位置 |
| --- | --- |
| 账库、运行状态、导入报告和日志 | %LOCALAPPDATA%\CustomerLedger |
| 备份 | %USERPROFILE%\Documents\客户账本备份（跟随 Windows“文档”重定向） |
| 导出文件 | %USERPROFILE%\Documents\客户账本导出（跟随 Windows“文档”重定向） |

程序目录只保存可替换的应用文件。更新前应先关闭程序，保留用户数据目录和备份目录，再整体替换 CustomerLedger 程序目录。程序通过 Windows 命名互斥体阻止同一用户重复启动。

## 首次启动与账库升级

启动时会准备用户目录，检查 SQLite 完整性，并在已有账库升级前保存升级前备份；随后只通过版本化迁移升级，不调用 create_all。迁移在正式账库上失败后，程序会先释放连接，再用升级前备份通过临时文件原子恢复正式账库，并重新检查完整性和结构版本，确认恢复成功后才停止启动。

如果迁移已经失败且自动恢复也失败，程序会写入持久保护标记 migration_rollback_failed，并阻止写入、导出等高风险操作；后续重启仍保持保护状态。请停止记账，保留账库和备份，联系管理员处理。

## 构建

在 Windows 开发环境执行：

    .\.venv\Scripts\python.exe -m pip install -e ".[dev,release,build]"
    powershell -ExecutionPolicy Bypass -File .\scripts\build_windows.ps1

构建脚本会先运行 pytest 和 ruff，清理本次构建专用的 build、dist 目录，生成 onedir 包，并检查包内没有 SQLite、Excel、备份或日志文件。

## 环境变量覆盖

管理员可通过以下环境变量把数据、备份、导出、日志、导入报告、迁移文件或保护标记指向指定位置。普通用户不需要设置这些变量：

CUSTOMER_LEDGER_DATA_ROOT、CUSTOMER_LEDGER_DATABASE_PATH、CUSTOMER_LEDGER_BACKUP_DIR、CUSTOMER_LEDGER_EXPORTS_DIR、CUSTOMER_LEDGER_LOG_DIR、CUSTOMER_LEDGER_IMPORT_REPORT_DIR、CUSTOMER_LEDGER_MIGRATIONS_DIR、CUSTOMER_LEDGER_SAFETY_LOCK_PATH、CUSTOMER_LEDGER_PORT。

端口变量仅用于管理员排查或自动化验收；正常使用保持默认随机端口，不要把服务端口暴露到局域网。
