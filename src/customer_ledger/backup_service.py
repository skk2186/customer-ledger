"""Consistent local SQLite backups, validation, retention and restore."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

BACKUP_FORMAT_VERSION = "1"
BACKUP_FILE_PREFIX = "ledger-"
BACKUP_KEEP_COUNT = 30
_SAFE_REASON = re.compile(r"[^A-Za-z0-9_-]+")


class BackupError(ValueError):
    """A safe, user-facing backup or restore failure."""


def safety_lock_exists(path: str | Path) -> bool:
    """Return whether the persistent write-protection marker exists."""

    try:
        return Path(path).resolve().exists()
    except OSError as exc:
        raise BackupError("无法检查账库保护状态，请停止记账并检查本机账库。") from exc


def write_safety_lock(
    path: str | Path,
    *,
    reason_code: str = "restore_rollback_failed",
    error_category: str = "restore",
) -> None:
    """Persist a non-sensitive marker after an unrecoverable restore failure."""

    target = Path(path).resolve()
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    payload = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "reason_code": reason_code,
        "error_category": error_category,
    }
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(temporary, target)
    except OSError as exc:
        _remove_if_exists(temporary)
        raise BackupError(
            "恢复失败，且安全保护标记无法保存；请立即停止操作并检查本机账库。"
        ) from exc


@dataclass(frozen=True)
class BackupManifest:
    manifest_filename: str
    database_filename: str
    created_at: str
    reason: str
    app_version: str
    schema_version: str
    file_size_bytes: int
    sha256: str
    valid: bool = True
    status: str = "有效"

    @property
    def created_date(self) -> str:
        return self.created_at[:10]


@dataclass(frozen=True)
class RestoreResult:
    restored: BackupManifest
    pre_restore: BackupManifest


def _database_path(bind) -> Path:
    if bind.dialect.name != "sqlite":
        raise BackupError("只有本机 SQLite 账库支持备份和恢复。")
    database = bind.engine.url.database
    if not database or database == ":memory:":
        raise BackupError("当前账库不是可备份的本地文件。")
    return Path(database).resolve()


def _open_connection(path: Path) -> sqlite3.Connection:
    try:
        return sqlite3.connect(str(path), timeout=5)
    except (OSError, sqlite3.Error) as exc:
        raise BackupError("无法打开备份文件，请选择其他有效备份。") from exc


def current_schema_version(path: str | Path) -> str:
    path = Path(path)
    connection = _open_connection(path)
    try:
        table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='alembic_version'"
        ).fetchone()
        if table is None:
            return "uninitialized"
        row = connection.execute("SELECT version_num FROM alembic_version LIMIT 1").fetchone()
        return str(row[0]) if row and row[0] else "uninitialized"
    except sqlite3.Error as exc:
        raise BackupError("备份文件的结构无法读取。") from exc
    finally:
        connection.close()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise BackupError("无法读取备份文件。") from exc
    return digest.hexdigest()


def _integrity_check(path: Path) -> None:
    connection = _open_connection(path)
    try:
        result = connection.execute("PRAGMA integrity_check").fetchone()
        if not result or str(result[0]).lower() != "ok":
            raise BackupError("备份文件完整性校验失败，不能恢复。")
    except sqlite3.Error as exc:
        raise BackupError("备份文件完整性校验失败，不能恢复。") from exc
    finally:
        connection.close()


def _manifest_from_dict(data: dict, filename: str) -> BackupManifest:
    required = {
        "format_version",
        "database_filename",
        "created_at",
        "reason",
        "app_version",
        "schema_version",
        "file_size_bytes",
        "sha256",
    }
    if not required.issubset(data):
        raise BackupError("备份清单不完整，不能恢复。")
    if str(data["format_version"]) != BACKUP_FORMAT_VERSION:
        raise BackupError("备份格式版本不兼容，不能恢复。")
    database_filename = str(data["database_filename"])
    if not database_filename or Path(database_filename).name != database_filename:
        raise BackupError("备份清单中的文件名无效。")
    try:
        file_size = int(data["file_size_bytes"])
    except (TypeError, ValueError) as exc:
        raise BackupError("备份清单中的文件大小无效。") from exc
    sha256 = str(data["sha256"])
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise BackupError("备份清单中的校验值无效。")
    return BackupManifest(
        manifest_filename=filename,
        database_filename=database_filename,
        created_at=str(data["created_at"]),
        reason=str(data["reason"]),
        app_version=str(data["app_version"]),
        schema_version=str(data["schema_version"]),
        file_size_bytes=file_size,
        sha256=sha256,
    )


def _read_manifest(manifest_path: Path) -> BackupManifest:
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError
        return _manifest_from_dict(data, manifest_path.name)
    except BackupError:
        raise
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise BackupError("备份清单无法读取，不能恢复。") from exc


def validate_backup(
    manifest_path: str | Path,
    *,
    expected_schema_version: str | None = None,
) -> BackupManifest:
    """Validate manifest, hash, SQLite open and integrity before recovery."""

    manifest_file = Path(manifest_path).resolve()
    manifest = _read_manifest(manifest_file)
    database_file = manifest_file.parent / manifest.database_filename
    if not database_file.is_file() or database_file.stat().st_size <= 0:
        raise BackupError("备份文件不存在或为空，不能恢复。")
    if database_file.stat().st_size != manifest.file_size_bytes:
        raise BackupError("备份文件大小与清单不一致，不能恢复。")
    if _sha256(database_file) != manifest.sha256:
        raise BackupError("备份文件校验失败，文件可能已损坏。")
    _integrity_check(database_file)
    actual_schema = current_schema_version(database_file)
    if actual_schema != manifest.schema_version:
        raise BackupError("备份文件结构与清单不一致，不能恢复。")
    if expected_schema_version is not None and actual_schema != expected_schema_version:
        raise BackupError("备份文件结构版本与当前系统不兼容，不能恢复。")
    return manifest


def _unique_destination(
    directory: Path,
    reason: str,
    *,
    now_provider: Callable[[], datetime] | None = None,
) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    current = now_provider() if now_provider is not None else datetime.now().astimezone()
    stamp = current.astimezone().strftime("%Y%m%d-%H%M%S")
    safe_reason = _SAFE_REASON.sub("-", reason).strip("-") or "manual"
    base = f"{BACKUP_FILE_PREFIX}{stamp}-{safe_reason}"
    for counter in range(1000):
        suffix = "" if counter == 0 else f"-{counter:02d}"
        database = directory / f"{base}{suffix}.db"
        manifest = directory / f"{base}{suffix}.json"
        if not database.exists() and not manifest.exists():
            return database, manifest
    raise BackupError("无法生成不覆盖旧文件的备份名称。")


def _destination_for(
    destination: str | Path | None,
    backup_dir: str | Path | None,
    reason: str,
    *,
    now_provider: Callable[[], datetime] | None = None,
) -> tuple[Path, Path]:
    if destination is None:
        if backup_dir is None:
            raise BackupError("未配置本机备份目录。")
        return _unique_destination(
            Path(backup_dir).resolve(), reason, now_provider=now_provider
        )
    database = Path(destination).resolve()
    if database.suffix.casefold() != ".db":
        raise BackupError("备份文件必须使用 .db 格式。")
    database.parent.mkdir(parents=True, exist_ok=True)
    manifest = database.with_suffix(".json")
    if database.exists() or manifest.exists():
        stem = database.stem
        for counter in range(1, 1000):
            suffix = f"-{counter:02d}"
            candidate = database.with_name(f"{stem}{suffix}.db")
            candidate_manifest = candidate.with_suffix(".json")
            if not candidate.exists() and not candidate_manifest.exists():
                return candidate, candidate_manifest
        raise BackupError("无法生成不覆盖旧文件的备份名称。")
    return database, manifest


def _online_backup(bind, target_path: Path) -> None:
    raw = bind.raw_connection()
    source = getattr(raw, "driver_connection", None) or getattr(raw, "connection", None)
    if not isinstance(source, sqlite3.Connection):
        raw.close()
        raise BackupError("无法取得本机账库连接，备份已阻止。")
    target = None
    try:
        target = sqlite3.connect(str(target_path), timeout=5)
        source.backup(target)
        target.commit()
    except (OSError, sqlite3.Error) as exc:
        raise BackupError("数据库备份失败，已阻止后续操作。") from exc
    finally:
        if target is not None:
            target.close()
        raw.close()


def _write_manifest(path: Path, manifest: BackupManifest) -> None:
    payload = {
        "format_version": BACKUP_FORMAT_VERSION,
        "database_filename": manifest.database_filename,
        "created_at": manifest.created_at,
        "reason": manifest.reason,
        "app_version": manifest.app_version,
        "schema_version": manifest.schema_version,
        "file_size_bytes": manifest.file_size_bytes,
        "sha256": manifest.sha256,
    }
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(temporary, path)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise BackupError("备份清单保存失败，已阻止后续操作。") from exc


def _remove_if_exists(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def prune_backups(
    backup_dir: str | Path, *, keep: int = BACKUP_KEEP_COUNT
) -> list[str]:
    """Remove only old valid application backup pairs in the configured directory."""

    directory = Path(backup_dir).resolve()
    if not directory.is_dir():
        return []
    valid: list[tuple[datetime, Path, Path]] = []
    for manifest_path in directory.glob(f"{BACKUP_FILE_PREFIX}*.json"):
        try:
            manifest = validate_backup(manifest_path)
            created = datetime.fromisoformat(manifest.created_at)
            valid.append(
                (created, manifest_path, manifest_path.parent / manifest.database_filename)
            )
        except (BackupError, OSError, ValueError):
            continue
    valid.sort(key=lambda item: item[0], reverse=True)
    errors: list[str] = []
    for _created, manifest_path, database_path in valid[keep:]:
        try:
            database_path.unlink()
            manifest_path.unlink()
        except OSError:
            errors.append("旧备份清理失败")
    return errors


def create_backup(
    bind,
    *,
    backup_dir: str | Path | None = None,
    destination: str | Path | None = None,
    reason: str = "manual",
    app_version: str = "stage-4",
    prune: bool = True,
    now_provider: Callable[[], datetime] | None = None,
) -> BackupManifest:
    """Create a consistent SQLite backup and its non-sensitive manifest."""

    source_path = _database_path(bind)
    if not source_path.is_file():
        raise BackupError("本机账库文件不存在，备份已阻止。")
    database_path, manifest_path = _destination_for(
        destination,
        backup_dir,
        reason,
        now_provider=now_provider,
    )
    temporary = database_path.with_name(f".{database_path.name}.{uuid.uuid4().hex}.tmp")
    installed = False
    try:
        _online_backup(bind, temporary)
        os.replace(temporary, database_path)
        installed = True
        file_size = database_path.stat().st_size
        schema_version = current_schema_version(source_path)
        manifest = BackupManifest(
            manifest_filename=manifest_path.name,
            database_filename=database_path.name,
            created_at=(
                now_provider() if now_provider is not None else datetime.now().astimezone()
            )
            .astimezone()
            .isoformat(timespec="seconds"),
            reason=reason,
            app_version=app_version,
            schema_version=schema_version,
            file_size_bytes=file_size,
            sha256=_sha256(database_path),
        )
        _write_manifest(manifest_path, manifest)
        validate_backup(manifest_path, expected_schema_version=schema_version)
        if prune:
            cleanup_errors = prune_backups(backup_dir or database_path.parent)
            if cleanup_errors:
                raise BackupError("旧备份清理失败，已阻止后续操作。")
        return manifest
    except BackupError:
        _remove_if_exists(temporary)
        if installed:
            _remove_if_exists(database_path)
            _remove_if_exists(manifest_path)
        raise
    except (OSError, sqlite3.Error) as exc:
        _remove_if_exists(temporary)
        if installed:
            _remove_if_exists(database_path)
            _remove_if_exists(manifest_path)
        raise BackupError("数据库备份失败，已阻止后续操作。") from exc


def list_backups(backup_dir: str | Path) -> list[BackupManifest]:
    directory = Path(backup_dir).resolve()
    if not directory.is_dir():
        return []
    values: list[BackupManifest] = []
    for path in directory.glob(f"{BACKUP_FILE_PREFIX}*.json"):
        try:
            manifest = _read_manifest(path)
            database_path = Path(manifest.database_filename)
            if (
                not manifest.database_filename.startswith(BACKUP_FILE_PREFIX)
                or database_path.suffix.casefold() != ".db"
                or database_path.stem != path.stem
            ):
                continue
            validate_backup(path)
        except (BackupError, OSError):
            try:
                manifest = _read_manifest(path)
                values.append(
                    BackupManifest(**{**manifest.__dict__, "valid": False, "status": "不可恢复"})
                )
            except BackupError:
                continue
        else:
            values.append(manifest)
    values.sort(key=lambda item: item.created_at, reverse=True)
    return values


def has_valid_daily_backup(backup_dir: str | Path, local_date: str) -> bool:
    """Return whether a valid daily backup already exists for this local date."""

    return any(
        item.valid and item.reason == "daily_startup" and item.created_date == local_date
        for item in list_backups(backup_dir)
    )


def restore_database(
    bind,
    manifest_path: str | Path,
    *,
    backup_dir: str | Path,
    app_version: str = "stage-4",
    post_restore_check: Callable[[], None] | None = None,
    safety_lock_path: str | Path | None = None,
) -> RestoreResult:
    """Restore atomically, with a pre-restore backup available for rollback."""

    source_path = _database_path(bind)
    backup_root = Path(backup_dir).resolve()
    manifest_file = Path(manifest_path).resolve()
    lock_path = (
        Path(safety_lock_path).resolve()
        if safety_lock_path is not None
        else source_path.parent / "WRITE_BLOCKED"
    )
    if manifest_file.parent != backup_root:
        raise BackupError("只能恢复应用备份目录中的备份文件。")
    current_schema = current_schema_version(source_path)
    selected = validate_backup(manifest_file, expected_schema_version=current_schema)
    selected_path = manifest_file.parent / selected.database_filename
    pre_restore = create_backup(
        bind,
        backup_dir=backup_dir,
        reason="before_restore",
        app_version=app_version,
        prune=False,
    )
    pre_path = Path(backup_dir).resolve() / pre_restore.database_filename
    temporary = source_path.with_name(f".{source_path.name}.{uuid.uuid4().hex}.restore")
    temporary_manifest = None
    try:
        shutil.copyfile(selected_path, temporary)
        temporary_manifest = _manifest_for_temporary(temporary, selected)
        validate_backup(temporary_manifest, expected_schema_version=current_schema)
        bind.dispose()
        os.replace(temporary, source_path)
        bind.dispose()
        _integrity_check(source_path)
        if current_schema_version(source_path) != current_schema:
            raise BackupError("恢复后的账库结构版本不兼容。")
        if post_restore_check is not None:
            post_restore_check()
        return RestoreResult(restored=selected, pre_restore=pre_restore)
    except Exception as exc:
        _remove_if_exists(temporary)
        try:
            rollback_temp = source_path.with_name(
                f".{source_path.name}.{uuid.uuid4().hex}.rollback"
            )
            shutil.copyfile(pre_path, rollback_temp)
            bind.dispose()
            os.replace(rollback_temp, source_path)
            bind.dispose()
            _integrity_check(source_path)
        except Exception as rollback_exc:
            _remove_if_exists(locals().get("rollback_temp", source_path.with_suffix(".rollback")))
            try:
                write_safety_lock(lock_path)
            except BackupError as lock_exc:
                raise BackupError(
                    "恢复失败，且自动回滚失败；安全保护标记也无法保存，请立即停止操作并检查本机账库。"
                ) from lock_exc
            raise BackupError(
                "恢复失败，且自动回滚失败；账库已进入保护状态，请停止继续记账并检查备份。"
            ) from rollback_exc
        raise BackupError("恢复失败，已自动回滚到恢复前状态。") from exc
    finally:
        _remove_if_exists(temporary)
        if temporary_manifest is not None:
            _remove_if_exists(temporary_manifest)


def _manifest_for_temporary(path: Path, source: BackupManifest) -> Path:
    """Create a temporary validation manifest without changing the selected backup."""

    manifest_path = path.with_suffix(".json")
    temporary = BackupManifest(
        manifest_filename=manifest_path.name,
        database_filename=path.name,
        created_at=source.created_at,
        reason=source.reason,
        app_version=source.app_version,
        schema_version=source.schema_version,
        file_size_bytes=path.stat().st_size,
        sha256=_sha256(path),
    )
    _write_manifest(manifest_path, temporary)
    return manifest_path
