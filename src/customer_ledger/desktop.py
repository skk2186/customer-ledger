"""Windows desktop entry point using Waitress and pywebview."""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

from flask_migrate import upgrade
from waitress.server import create_server

from . import create_app, db
from .backup_service import (
    BackupError,
    check_database_integrity,
    create_backup,
    current_schema_version,
    safety_lock_exists,
    write_safety_lock,
)
from .runtime_paths import (
    RuntimePaths,
    ensure_runtime_directories,
    resolve_runtime_paths,
)
from .settings_service import load_export_directory
from .version import __version__

APP_TITLE = f"客户快捷填表系统 {__version__}"
MUTEX_NAME = r"Local\CustomerLedgerDesktop"


class StartupError(RuntimeError):
    """A user-facing desktop startup failure."""


class DesktopApi:
    """Small native helpers exposed to the local desktop window."""

    def __init__(self, app) -> None:
        self.app = app

    def choose_export_directory(self) -> str:
        """Return a selected folder, or an empty string when the dialog is cancelled."""

        try:
            import webview

            windows = getattr(webview, "windows", [])
            if not windows:
                return ""
            selected = windows[0].create_file_dialog(
                webview.FOLDER_DIALOG,
                allow_multiple=False,
            )
            if not selected:
                return ""
            return str(Path(selected[0]).resolve())
        except (ImportError, OSError, TypeError, AttributeError):
            return ""

    def open_export_directory(self) -> bool:
        """Open the configured export folder in the native file manager."""

        try:
            directory = Path(self.app.config["EXPORTS_DIR"]).resolve()
            directory.mkdir(parents=True, exist_ok=True)
            if os.name == "nt":
                os.startfile(str(directory))
            else:
                import webbrowser

                webbrowser.open(directory.as_uri())
            return True
        except (OSError, ValueError):
            return False


class SingleInstance:
    """A Windows named mutex whose lifetime is owned by this process."""

    def __init__(self, name: str = MUTEX_NAME) -> None:
        self.name = name
        self._handle = None

    def acquire(self) -> bool:
        if os.name != "nt":
            raise StartupError("桌面版只能在 Windows 上运行。")
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        handle = kernel32.CreateMutexW(None, True, self.name)
        if not handle:
            raise StartupError("无法建立程序单实例保护，请稍后重试。")
        if ctypes.get_last_error() == 183:
            kernel32.CloseHandle(handle)
            return False
        self._handle = handle
        return True

    def release(self) -> None:
        if self._handle is None:
            return
        ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(self._handle)
        self._handle = None

    def __enter__(self) -> "SingleInstance":
        if not self.acquire():
            raise StartupError("客户快捷填表系统已经在运行。")
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
        self.release()


def _startup_error(message: str) -> StartupError:
    return StartupError(message)


def _dispose_database(app) -> None:
    with app.app_context():
        db.session.rollback()
        db.session.remove()
        db.engine.dispose()


def _restore_database_from_backup(
    app,
    paths: RuntimePaths,
    backup_manifest,
    expected_schema_version: str,
) -> None:
    """Restore the exact pre-migration database through a checked temp file."""

    backup_path = paths.backup_root / backup_manifest.database_filename
    if not backup_path.is_file():
        raise BackupError("升级前备份文件不存在，无法恢复原账库。")
    temporary = paths.database_path.with_name(
        f".{paths.database_path.name}.{uuid.uuid4().hex}.migration-rollback"
    )
    try:
        _dispose_database(app)
        shutil.copyfile(backup_path, temporary)
        check_database_integrity(temporary)
        if current_schema_version(temporary) != expected_schema_version:
            raise BackupError("升级前备份的结构版本不符合预期。")
        os.replace(temporary, paths.database_path)
        check_database_integrity(paths.database_path)
        if current_schema_version(paths.database_path) != expected_schema_version:
            raise BackupError("恢复后的账库结构版本不符合预期。")
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def initialize_database(app, paths: RuntimePaths) -> None:
    """Validate, back up and migrate a database without create_all()."""

    database_exists = paths.database_path.is_file()
    before_migration_backup = None
    if database_exists:
        try:
            with app.app_context():
                check_database_integrity(paths.database_path)
                current_schema_version(paths.database_path)
                before_migration_backup = create_backup(
                    db.engine,
                    backup_dir=paths.backup_root,
                    reason="before_migration",
                    app_version=app.config["APP_VERSION"],
                )
        except BackupError as exc:
            raise _startup_error("账库检查或升级前备份失败，已停止启动并保留原有账库。") from exc

    try:
        with app.app_context():
            upgrade(directory=str(paths.migrations_root))
    except Exception as exc:
        if before_migration_backup is not None:
            try:
                _restore_database_from_backup(
                    app,
                    paths,
                    before_migration_backup,
                    before_migration_backup.schema_version,
                )
            except Exception as rollback_exc:
                try:
                    write_safety_lock(
                        paths.safety_lock_path,
                        reason_code="migration_rollback_failed",
                        error_category="migration",
                    )
                except BackupError as lock_exc:
                    raise _startup_error(
                        "账库升级和自动恢复均失败，保护标记也无法保存；请立即停止操作。"
                    ) from lock_exc
                raise _startup_error(
                    "账库升级和自动恢复均失败，账库已进入保护状态。"
                ) from rollback_exc
        raise _startup_error("账库升级失败，已恢复到升级前状态；启动已停止。") from exc
    finally:
        try:
            _dispose_database(app)
        except Exception:
            pass

    try:
        check_database_integrity(paths.database_path)
        if current_schema_version(paths.database_path) == "uninitialized":
            raise BackupError("账库结构未完成升级。")
    except BackupError as exc:
        raise _startup_error("账库升级后的结构或完整性检查失败，已停止启动。") from exc


def prepare_desktop_application(
    *,
    env=None,
    frozen: bool | None = None,
    meipass: str | Path | None = None,
    documents_root: str | Path | None = None,
):
    """Resolve paths, check the safety marker, migrate, and return the app."""

    paths = resolve_runtime_paths(
        env,
        frozen=frozen,
        meipass=meipass,
        documents_root=documents_root,
    )
    try:
        ensure_runtime_directories(paths)
        locked = safety_lock_exists(paths.safety_lock_path)
    except (BackupError, OSError) as exc:
        raise _startup_error("无法准备本机数据目录，请检查目录权限后重试。") from exc
    app_config = paths.app_config()
    app_config["EXPORTS_DIR"] = str(
        load_export_directory(paths.settings_path, paths.export_root)
    )
    app = create_app(app_config)
    if not locked:
        initialize_database(app, paths)
    return app, paths


class LocalWsgiServer:
    """A small Waitress controller with event and health-check readiness."""

    def __init__(
        self,
        application,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        ready_timeout: float = 15.0,
    ) -> None:
        if host != "127.0.0.1":
            raise ValueError("桌面服务只能监听 127.0.0.1。")
        self.application = application
        self.host = host
        self.requested_port = port
        self.ready_timeout = ready_timeout
        self.port: int | None = None
        self.ready = threading.Event()
        self.stopped = threading.Event()
        self._server = None
        self._thread: threading.Thread | None = None
        self._error: Exception | None = None

    @property
    def url(self) -> str:
        if self.port is None:
            raise StartupError("本地服务尚未就绪。")
        return f"http://{self.host}:{self.port}"

    def start(self) -> None:
        def serve() -> None:
            try:
                server = create_server(
                    self.application,
                    host=self.host,
                    port=self.requested_port,
                    _start=True,
                )
                self._server = server
                self.port = server.effective_port
                self.ready.set()
                server.run()
            except Exception as exc:
                self._error = exc
                self.ready.set()
            finally:
                self.stopped.set()

        self._thread = threading.Thread(target=serve, name="customer-ledger-wsgi", daemon=True)
        self._thread.start()
        if not self.ready.wait(self.ready_timeout):
            self.close()
            raise _startup_error("本地服务启动超时，请稍后重试。")
        if self._error is not None:
            raise _startup_error("本地服务启动失败，请稍后重试。")

    def wait_until_ready(self, path: str = "/healthz") -> None:
        if not self.ready.wait(self.ready_timeout):
            raise _startup_error("本地服务没有报告就绪状态。")
        deadline = time.monotonic() + self.ready_timeout
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(self.url + path, timeout=0.2) as response:
                    if response.status == 200:
                        return
            except (OSError, urllib.error.URLError):
                if self.stopped.is_set() and self._error is not None:
                    break
            time.sleep(0.1)
        raise _startup_error("本地服务健康检查未通过，请稍后重试。")

    def close(self) -> None:
        if self._server is not None:
            self._server.close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._server = None


def show_user_error(message: str) -> None:
    """Show a Chinese error without exposing exception details or a console."""

    if os.name == "nt":
        ctypes.windll.user32.MessageBoxW(None, message, APP_TITLE, 0x10)
    else:
        print(message)


def _desktop_port() -> int:
    raw_port = os.environ.get("CUSTOMER_LEDGER_PORT", "0")
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise _startup_error("本机服务端口设置无效，请联系管理员。") from exc
    if not 0 <= port <= 65535:
        raise _startup_error("本机服务端口设置无效，请联系管理员。")
    return port


def _write_startup_diagnostic(error: BaseException) -> None:
    try:
        paths = resolve_runtime_paths()
        ensure_runtime_directories(paths)
        diagnostic = paths.logs_root / "startup.log"
        if isinstance(error, BackupError):
            error_category = "backup"
            safe_summary = "账库或备份检查失败。"
        elif isinstance(error, OSError):
            error_category = "filesystem"
            safe_summary = "本机文件操作失败。"
        elif isinstance(error, StartupError):
            error_category = "startup"
            safe_summary = "启动检查未通过。"
        else:
            error_category = "startup"
            safe_summary = "启动失败，请检查安装文件和本机数据目录。"
        payload = {
            "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
            "app_version": __version__,
            "error_category": error_category,
            "error_type": type(error).__name__,
            "safe_summary": safe_summary,
        }
        diagnostic.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
    except Exception:
        pass


def run_desktop() -> int:
    instance = SingleInstance()
    server = None
    try:
        if not instance.acquire():
            show_user_error("客户快捷填表系统已经在运行。")
            return 1
        app, paths = prepare_desktop_application()
        _ = paths
        server = LocalWsgiServer(app, port=_desktop_port())
        server.start()
        server.wait_until_ready()
        try:
            import webview
        except ImportError as exc:
            raise _startup_error("桌面窗口组件未安装，无法启动客户快捷填表系统。") from exc
        webview.create_window(
            APP_TITLE,
            server.url,
            width=1280,
            height=820,
            min_size=(900, 600),
            js_api=DesktopApi(app),
        )
        webview.start(debug=False)
        return 0
    except StartupError as exc:
        _write_startup_diagnostic(exc)
        show_user_error(str(exc))
        return 1
    except Exception as exc:
        _write_startup_diagnostic(exc)
        show_user_error("客户快捷填表系统启动失败，请联系管理员检查安装文件和本机数据目录。")
        return 1
    finally:
        if server is not None:
            server.close()
        instance.release()


if __name__ == "__main__":
    raise SystemExit(run_desktop())
