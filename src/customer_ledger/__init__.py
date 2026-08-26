"""Application factory for the local customer ledger."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from flask import Flask, render_template, request
from sqlalchemy.exc import SQLAlchemyError

from .audit_service import add_system_audit
from .backup_service import (
    BackupError,
    create_backup,
    has_valid_daily_backup,
    safety_lock_exists,
)
from .extensions import db, migrate


def create_app(test_config: dict | None = None) -> Flask:
    """Create a configured application without creating database tables implicitly."""

    app = Flask(__name__)
    project_root = Path(__file__).resolve().parents[2]
    default_db_path = project_root / "runtime_data" / "customer_ledger.db"
    default_db_path.parent.mkdir(parents=True, exist_ok=True)

    app.config.from_mapping(
        SECRET_KEY=os.environ.get("CUSTOMER_LEDGER_SECRET_KEY", "local-development-only"),
        SQLALCHEMY_DATABASE_URI=os.environ.get(
            "SQLALCHEMY_DATABASE_URI", f"sqlite:///{default_db_path}"
        ),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        MIGRATIONS_DIR=str(project_root / "migrations"),
        EXPORTS_DIR=os.environ.get("CUSTOMER_LEDGER_EXPORTS_DIR", str(project_root / "exports")),
        IMPORT_REPORT_DIR=os.environ.get(
            "CUSTOMER_LEDGER_IMPORT_REPORT_DIR",
            str(project_root / "runtime_data" / "import_reports"),
        ),
        BACKUP_DIR=os.environ.get("CUSTOMER_LEDGER_BACKUP_DIR", str(project_root / "backups")),
        SAFETY_LOCK_PATH=os.environ.get(
            "CUSTOMER_LEDGER_SAFETY_LOCK_PATH",
            str(project_root / "runtime_data" / "WRITE_BLOCKED"),
        ),
        APP_VERSION="stage-4",
        LEDGER_PAGE_SIZE=50,
        JSON_AS_ASCII=False,
    )
    if test_config:
        app.config.update(test_config)

    # Import models before Alembic inspects metadata.
    from . import models  # noqa: F401

    db.init_app(app)
    migrate.init_app(app, db, directory=app.config["MIGRATIONS_DIR"])

    from .routes import main_bp

    app.register_blueprint(main_bp)

    @app.template_filter("yuan")
    def yuan_filter(cents: int) -> str:
        sign = "-" if cents < 0 else ""
        cents = abs(cents)
        return f"{sign}{cents // 100}.{cents % 100:02d}"

    try:
        app.extensions["safety_locked"] = safety_lock_exists(app.config["SAFETY_LOCK_PATH"])
    except BackupError:
        app.extensions["safety_locked"] = True
    app.extensions["daily_backup_date"] = None

    def _local_now() -> datetime:
        provider = app.config.get("LOCAL_NOW_PROVIDER")
        current = provider() if provider is not None else datetime.now().astimezone()
        return current.astimezone()

    @app.before_request
    def _check_safety_lock():
        """Block all state-changing requests while persistent protection is active."""

        try:
            locked = safety_lock_exists(app.config["SAFETY_LOCK_PATH"])
        except BackupError:
            locked = True
        app.extensions["safety_locked"] = locked
        locked_get_endpoints = {
            "main.export_customer",
            "main.export_summary",
            "main.export_all_ledgers",
        }
        if locked and (
            request.method not in {"GET", "HEAD", "OPTIONS"}
            or request.endpoint in locked_get_endpoints
        ):
            return (
                render_template(
                    "error.html",
                    title="账库处于保护状态",
                    message="账库处于保护状态。上次恢复未能安全完成。请停止继续记账并检查备份。",
                ),
                503,
            )
        return None

    @app.before_request
    def _ensure_daily_backup():
        """Create one consistent local backup before the first request of each day."""

        if app.extensions.get("safety_locked"):
            return None
        today = _local_now().date().isoformat()
        if app.extensions.get("daily_backup_date") == today:
            return None
        if has_valid_daily_backup(app.config["BACKUP_DIR"], today):
            app.extensions["daily_backup_date"] = today
            return None
        try:
            create_backup(
                db.engine,
                backup_dir=app.config["BACKUP_DIR"],
                reason="daily_startup",
                app_version=app.config["APP_VERSION"],
                now_provider=_local_now,
            )
            db.session.rollback()
            add_system_audit(
                db.session,
                "backup",
                "daily_startup",
                counts={"requests": 1},
            )
            db.session.commit()
            app.extensions["daily_backup_date"] = today
        except (BackupError, SQLAlchemyError):
            db.session.rollback()
            return "本机每日备份失败，已暂停本次操作；请检查备份目录后重试。", 503
        return None

    @app.errorhandler(404)
    def _not_found(_error):
        db.session.rollback()
        return render_template("error.html", title="页面不存在", message="没有找到这页内容。"), 404

    @app.errorhandler(500)
    def _internal_error(_error):
        db.session.rollback()
        return (
            render_template(
                "error.html", title="操作未完成", message="系统暂时无法完成操作，请稍后重试。"
            ),
            500,
        )

    return app


__all__ = ["create_app", "db"]
