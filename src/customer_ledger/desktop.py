"""Windows desktop entry point using Waitress and pywebview."""

from __future__ import annotations

import ctypes
import os
import threading
import time
import traceback
import urllib.error
import urllib.request
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
)
from .runtime_paths import (
    RuntimePaths,
    ensure_runtime_directories,
    resolve_runtime_paths,
)
from .version import __version__

APP_TITLE = f"客户快捷填表系统 {__version__}"
MUTEX_NAME = r"Local\CustomerLedgerDesktop"


class StartupError(RuntimeError):
    """A user-facing desktop startup failure."""


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


def initialize_database(app, paths: RuntimePaths) -> None:
    """Validate, back up and migrate a database without create_all()."""

    database_exists = paths.database_path.is_file()
    if database_exists:
        try:
            with app.app_context():
                check_database_integrity(paths.database_path)
                current_schema_version(paths.database_path)
                create_backup(
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
        with app.app_context():
            db.session.rollback()
            db.session.remove()
            db.engine.dispose()
        raise _startup_error("账库升级失败，已停止启动；请保留原有账库和升级前备份。") from exc
    finally:
        with app.app_context():
            db.session.remove()
            db.engine.dispose()

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
        safety_lock_exists(paths.safety_lock_path)
    except (BackupError, OSError) as exc:
        raise _startup_error("无法准备本机数据目录，请检查目录权限后重试。") from exc
    app = create_app(paths.app_config())
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
        diagnostic.write_text(
            f"{type(error).__name__}: {error}\n{traceback.format_exc()}",
            encoding="utf-8",
        )
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
        webview.create_window(APP_TITLE, server.url, width=1280, height=820, min_size=(900, 600))
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
