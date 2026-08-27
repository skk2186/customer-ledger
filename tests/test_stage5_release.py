from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest
from flask import Flask

import customer_ledger.desktop as desktop
from customer_ledger import create_app
from customer_ledger.backup_service import current_schema_version, list_backups
from customer_ledger.desktop import (
    LocalWsgiServer,
    SingleInstance,
    StartupError,
    initialize_database,
)
from customer_ledger.extensions import db
from customer_ledger.runtime_paths import (
    ensure_runtime_directories,
    resolve_documents_root,
    resolve_runtime_paths,
)

PROJECT_ROOT = Path(__file__).parents[1]
SOURCE_FILE = PROJECT_ROOT / "src" / "customer_ledger" / "runtime_paths.py"


def test_frozen_runtime_paths_keep_resources_and_user_data_separate(tmp_path):
    local_app_data = tmp_path / "LocalAppData"
    documents = tmp_path / "Documents"
    resources = tmp_path / "onedir"
    paths = resolve_runtime_paths(
        {
            "LOCALAPPDATA": str(local_app_data),
            "USERPROFILE": str(tmp_path / "Profile"),
        },
        frozen=True,
        meipass=resources,
        documents_root=documents,
    )

    assert paths.resource_root == resources.resolve()
    assert paths.package_root == (resources / "customer_ledger").resolve()
    assert paths.data_root == (local_app_data / "CustomerLedger").resolve()
    assert paths.database_path == paths.data_root / "data" / "customer_ledger.db"
    assert paths.backup_root == (documents / "客户账本备份").resolve()
    assert paths.export_root == (documents / "客户账本导出").resolve()
    assert paths.safety_lock_path == paths.data_root / "runtime" / "WRITE_BLOCKED"
    assert paths.resource_root != paths.data_root


def test_development_runtime_paths_preserve_repository_defaults(tmp_path):
    paths = resolve_runtime_paths(
        {},
        frozen=False,
        source_file=SOURCE_FILE,
        documents_root=tmp_path / "Documents",
    )

    assert paths.resource_root == PROJECT_ROOT.resolve()
    assert paths.package_root == (PROJECT_ROOT / "src" / "customer_ledger").resolve()
    assert paths.data_root == (PROJECT_ROOT / "runtime_data").resolve()
    assert paths.backup_root == (PROJECT_ROOT / "backups").resolve()
    assert paths.export_root == (PROJECT_ROOT / "exports").resolve()
    assert paths.migrations_root == (PROJECT_ROOT / "migrations").resolve()


def _temporary_paths(tmp_path):
    return resolve_runtime_paths(
        {
            "CUSTOMER_LEDGER_DATA_ROOT": str(tmp_path / "data-root"),
            "CUSTOMER_LEDGER_BACKUP_DIR": str(tmp_path / "backups"),
            "CUSTOMER_LEDGER_EXPORTS_DIR": str(tmp_path / "exports"),
            "CUSTOMER_LEDGER_IMPORT_REPORT_DIR": str(tmp_path / "import-reports"),
            "CUSTOMER_LEDGER_LOG_DIR": str(tmp_path / "logs"),
            "CUSTOMER_LEDGER_SAFETY_LOCK_PATH": str(tmp_path / "runtime" / "WRITE_BLOCKED"),
        },
        frozen=False,
        source_file=SOURCE_FILE,
        documents_root=tmp_path / "Documents",
    )


def test_desktop_database_initialization_migrates_and_backs_up_existing_database(tmp_path):
    paths = _temporary_paths(tmp_path)
    ensure_runtime_directories(paths)
    app = create_app({**paths.app_config(), "TESTING": True})

    initialize_database(app, paths)
    assert paths.database_path.is_file()
    assert current_schema_version(paths.database_path) != "uninitialized"

    initialize_database(app, paths)
    assert any(item.reason == "before_migration" for item in list_backups(paths.backup_root))

    with app.app_context():
        db.session.remove()
        db.engine.dispose()


def test_migration_failure_preserves_database_and_upgrade_backup(tmp_path, monkeypatch):
    paths = _temporary_paths(tmp_path)
    ensure_runtime_directories(paths)
    app = create_app({**paths.app_config(), "TESTING": True})
    initialize_database(app, paths)

    def fail_upgrade(**_kwargs):
        raise RuntimeError("synthetic migration failure")

    monkeypatch.setattr(desktop, "upgrade", fail_upgrade)
    with pytest.raises(StartupError, match="账库升级失败"):
        initialize_database(app, paths)

    assert paths.database_path.is_file()
    assert any(item.reason == "before_migration" for item in list_backups(paths.backup_root))
    with app.app_context():
        db.session.remove()
        db.engine.dispose()


@pytest.mark.skipif(os.name != "nt", reason="Windows named mutex is a release-only guard")
def test_single_instance_mutex_has_process_lifetime():
    name = rf"Local\CustomerLedgerTest-{uuid4().hex}"
    first = SingleInstance(name)
    second = SingleInstance(name)
    third = SingleInstance(name)

    assert first.acquire() is True
    try:
        assert second.acquire() is False
    finally:
        second.release()
        first.release()

    assert third.acquire() is True
    third.release()


def test_local_wsgi_server_binds_loopback_and_closes_cleanly():
    application = Flask("stage5-test")

    @application.get("/healthz")
    def healthz():
        return {"status": "ok"}

    server = LocalWsgiServer(application, port=0)
    server.start()
    try:
        server.wait_until_ready()
        assert server.host == "127.0.0.1"
        assert server.port is not None
        assert server.url.startswith("http://127.0.0.1:")
    finally:
        server.close()

    assert server.stopped.is_set()


def test_windows_release_configuration_keeps_packaged_resources_outside_user_data():
    spec = (PROJECT_ROOT / "customer_ledger.spec").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "scripts" / "build_windows.ps1").read_text(encoding="utf-8")

    assert 'name="CustomerLedger"' in spec
    assert "console=False" in spec
    assert '"migrations"' in spec
    assert "runtime_data" not in spec
    assert "Remove-Item -LiteralPath" in script
    assert "CustomerLedger.exe" in script
    assert "private_samples" in script


def test_documents_resolution_falls_back_without_external_services(tmp_path):
    result = resolve_documents_root(
        {"USERPROFILE": str(tmp_path / "Profile")},
        use_windows_api=False,
    )
    assert result == (tmp_path / "Profile" / "Documents").resolve()
