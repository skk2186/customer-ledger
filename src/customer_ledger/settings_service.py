"""Small, non-accounting settings store for desktop user preferences."""

from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4


class SettingsError(ValueError):
    """A user-correctable settings or export-directory error."""


def load_export_directory(settings_path: str | Path, default_directory: str | Path) -> Path:
    """Read only the export-directory setting and safely fall back when invalid."""

    default = Path(default_directory).expanduser().resolve()
    try:
        payload = json.loads(Path(settings_path).read_text(encoding="utf-8"))
        value = payload.get("export_directory") if isinstance(payload, dict) else None
        if not isinstance(value, str) or not value.strip():
            raise ValueError("invalid export directory setting")
        return Path(value).expanduser().resolve()
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return default


def validate_export_directory(directory: str | Path) -> Path:
    """Create a selected folder and verify that a small temporary file can be written."""

    candidate = Path(directory).expanduser().resolve()
    try:
        if candidate.exists() and not candidate.is_dir():
            raise SettingsError("导出位置不是文件夹，请重新选择。")
        candidate.mkdir(parents=True, exist_ok=True)
        if not candidate.is_dir():
            raise SettingsError("导出位置不是文件夹，请重新选择。")
        probe = candidate / f".customer-ledger-write-test-{uuid4().hex}.tmp"
        with probe.open("x", encoding="utf-8") as handle:
            handle.write("ok")
        probe.unlink()
    except SettingsError:
        raise
    except OSError as exc:
        raise SettingsError("导出文件夹不可写，请选择其他文件夹。") from exc
    return candidate


def save_export_directory(settings_path: str | Path, directory: str | Path) -> Path:
    """Atomically save the one supported preference as UTF-8 JSON."""

    target = Path(settings_path).expanduser().resolve()
    value = Path(directory).expanduser().resolve()
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump({"export_directory": str(value)}, handle, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except OSError as exc:
        raise SettingsError("导出设置保存失败，请检查本机数据目录权限。") from exc
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return value
