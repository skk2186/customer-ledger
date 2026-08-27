"""Application factory for the local customer ledger."""

from __future__ import annotations

import os
from datetime import datetime

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
from .runtime_paths import resolve_runtime_paths
from .settings_service import load_export_directory
from .version import __version__


def create_app(test_config: dict | None = None) -> Flask:
    """Create a configured application without creating database tables implicitly."""

    paths = resolve_runtime_paths()
    paths.data_dir.mkdir(parents=True, exist_ok=True)
    app = Flask(
        __name__,
        template_folder=str(paths.package_root / "templates"),
        static_folder=str(paths.package_root / "static"),
    )
    defaults = paths.app_config()
    if not os.environ.get("CUSTOMER_LEDGER_EXPORTS_DIR"):
        defaults["EXPORTS_DIR"] = str(
            load_export_directory(paths.settings_path, paths.export_root)
        )
    if os.environ.get("SQLALCHEMY_DATABASE_URI"):
        defaults["SQLALCHEMY_DATABASE_URI"] = os.environ["SQLALCHEMY_DATABASE_URI"]

    app.config.from_mapping(
        defaults,
        SECRET_KEY=os.environ.get("CUSTOMER_LEDGER_SECRET_KEY", "local-development-only"),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        APP_VERSION=__version__,
        LEDGER_PAGE_SIZE=50,
        JSON_AS_ASCII=False,
    )
    if test_config:
        app.config.update(test_config)
        if "DEFAULT_EXPORTS_DIR" not in test_config and "EXPORTS_DIR" in test_config:
            app.config["DEFAULT_EXPORTS_DIR"] = app.config["EXPORTS_DIR"]
    if not test_config or "EXPORTS_DIR" not in test_config:
        app.config["EXPORTS_DIR"] = str(
            load_export_directory(
                app.config["SETTINGS_PATH"], app.config["DEFAULT_EXPORTS_DIR"]
            )
        )

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
