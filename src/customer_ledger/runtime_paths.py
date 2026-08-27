"""Resource and user-data path resolution for development and frozen builds."""

from __future__ import annotations

import ctypes
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

APP_DATA_NAME = "CustomerLedger"
BACKUP_FOLDER_NAME = "客户账本备份"
EXPORT_FOLDER_NAME = "客户账本导出"


@dataclass(frozen=True)
class RuntimePaths:
    resource_root: Path
    package_root: Path
    migrations_root: Path
    data_root: Path
    data_dir: Path
    runtime_root: Path
    import_reports_root: Path
    logs_root: Path
    database_path: Path
    backup_root: Path
    export_root: Path
    settings_path: Path
    safety_lock_path: Path
    frozen: bool

    @property
    def database_uri(self) -> str:
        return f"sqlite:///{self.database_path.as_posix()}"

    def app_config(self) -> dict[str, str]:
        return {
            "RESOURCE_ROOT": str(self.resource_root),
            "PACKAGE_ROOT": str(self.package_root),
            "MIGRATIONS_DIR": str(self.migrations_root),
            "DATA_ROOT": str(self.data_root),
            "RUNTIME_ROOT": str(self.runtime_root),
            "IMPORT_REPORT_DIR": str(self.import_reports_root),
            "LOG_DIR": str(self.logs_root),
            "SQLALCHEMY_DATABASE_URI": self.database_uri,
            "BACKUP_DIR": str(self.backup_root),
            "EXPORTS_DIR": str(self.export_root),
            "DEFAULT_EXPORTS_DIR": str(self.export_root),
            "SETTINGS_PATH": str(self.settings_path),
            "SAFETY_LOCK_PATH": str(self.safety_lock_path),
        }


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def resolve_resource_root(
    *,
    frozen: bool | None = None,
    meipass: str | Path | None = None,
    source_file: str | Path | None = None,
) -> Path:
    frozen = is_frozen() if frozen is None else frozen
    if frozen:
        return Path(meipass or getattr(sys, "_MEIPASS", Path(sys.executable).parent)).resolve()
    package_file = Path(source_file or __file__).resolve()
    return package_file.parent.parent.parent


def resolve_package_root(
    *,
    resource_root: str | Path,
    frozen: bool | None = None,
    source_file: str | Path | None = None,
) -> Path:
    frozen = is_frozen() if frozen is None else frozen
    if frozen:
        return Path(resource_root).resolve() / "customer_ledger"
    return Path(source_file or __file__).resolve().parent


def resolve_documents_root(
    env: Mapping[str, str] | None = None,
    *,
    use_windows_api: bool | None = None,
) -> Path:
    """Resolve the real Windows Documents folder, with a deterministic fallback."""

    env = os.environ if env is None else env
    if use_windows_api is None:
        use_windows_api = os.name == "nt"
    if use_windows_api and os.name == "nt":
        buffer = ctypes.create_unicode_buffer(32768)
        shell32 = ctypes.windll.shell32
        if shell32.SHGetFolderPathW(None, 5, None, 0, buffer) == 0 and buffer.value:
            return Path(buffer.value).resolve()
    profile = env.get("USERPROFILE") or env.get("HOME")
    return (Path(profile) if profile else Path.home()) / "Documents"


def _local_app_data(env: Mapping[str, str]) -> Path:
    value = env.get("LOCALAPPDATA")
    if value:
        return Path(value)
    profile = env.get("USERPROFILE") or env.get("HOME")
    return (Path(profile) if profile else Path.home()) / "AppData" / "Local"


def resolve_runtime_paths(
    env: Mapping[str, str] | None = None,
    *,
    frozen: bool | None = None,
    meipass: str | Path | None = None,
    source_file: str | Path | None = None,
    documents_root: str | Path | None = None,
) -> RuntimePaths:
    env = os.environ if env is None else env
    frozen = is_frozen() if frozen is None else frozen
    resource_root = resolve_resource_root(
        frozen=frozen, meipass=meipass, source_file=source_file
    )
    package_root = resolve_package_root(
        resource_root=resource_root, frozen=frozen, source_file=source_file
    )
    project_root = resource_root

    if env.get("CUSTOMER_LEDGER_DATA_ROOT"):
        data_root = Path(env["CUSTOMER_LEDGER_DATA_ROOT"]).resolve()
    elif frozen:
        data_root = (_local_app_data(env) / APP_DATA_NAME).resolve()
    else:
        data_root = (project_root / "runtime_data").resolve()
    data_dir = data_root / "data"
    runtime_root = data_root / "runtime"
    import_reports_root = Path(
        env.get("CUSTOMER_LEDGER_IMPORT_REPORT_DIR", data_root / "import_reports")
    ).resolve()
    logs_root = Path(env.get("CUSTOMER_LEDGER_LOG_DIR", data_root / "logs")).resolve()
    database_path = Path(
        env.get("CUSTOMER_LEDGER_DATABASE_PATH", data_dir / "customer_ledger.db")
    ).resolve()

    if documents_root is None:
        documents_root = resolve_documents_root(env)
    documents_root = Path(documents_root).resolve()
    backup_root = Path(
        env.get(
            "CUSTOMER_LEDGER_BACKUP_DIR",
            documents_root / BACKUP_FOLDER_NAME if frozen else project_root / "backups",
        )
    ).resolve()
    export_root = Path(
        env.get(
            "CUSTOMER_LEDGER_EXPORTS_DIR",
            documents_root / EXPORT_FOLDER_NAME if frozen else project_root / "exports",
        )
    ).resolve()
    settings_path = Path(
        env.get("CUSTOMER_LEDGER_SETTINGS_PATH", data_root / "settings.json")
    ).resolve()
    migrations_root = Path(
        env.get(
            "CUSTOMER_LEDGER_MIGRATIONS_DIR",
            resource_root / "migrations" if frozen else project_root / "migrations",
        )
    ).resolve()
    safety_lock_path = Path(
        env.get("CUSTOMER_LEDGER_SAFETY_LOCK_PATH", runtime_root / "WRITE_BLOCKED")
    ).resolve()
    return RuntimePaths(
        resource_root=resource_root,
        package_root=package_root,
        migrations_root=migrations_root,
        data_root=data_root,
        data_dir=data_dir,
        runtime_root=runtime_root,
        import_reports_root=import_reports_root,
        logs_root=logs_root,
        database_path=database_path,
        backup_root=backup_root,
        export_root=export_root,
        settings_path=settings_path,
        safety_lock_path=safety_lock_path,
        frozen=frozen,
    )


def ensure_runtime_directories(paths: RuntimePaths) -> None:
    for directory in (
        paths.data_root,
        paths.data_dir,
        paths.runtime_root,
        paths.import_reports_root,
        paths.logs_root,
        paths.backup_root,
        paths.export_root,
    ):
        directory.mkdir(parents=True, exist_ok=True)
