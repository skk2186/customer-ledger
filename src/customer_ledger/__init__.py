"""Application factory for the local customer ledger."""

from __future__ import annotations

import os
from pathlib import Path

from flask import Flask

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

    return app


__all__ = ["create_app", "db"]
