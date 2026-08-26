from __future__ import annotations

import pytest
from flask_migrate import upgrade

from customer_ledger import create_app, db


@pytest.fixture()
def app(tmp_path):
    database_path = tmp_path / "test.db"
    app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{database_path}",
            "BACKUP_DIR": str(tmp_path / "backups"),
            "SAFETY_LOCK_PATH": str(tmp_path / "WRITE_BLOCKED"),
            "EXPORTS_DIR": str(tmp_path / "exports"),
            "IMPORT_REPORT_DIR": str(tmp_path / "import_reports"),
        }
    )
    with app.app_context():
        upgrade()
    yield app
    with app.app_context():
        db.session.remove()
        db.engine.dispose()


@pytest.fixture()
def client(app):
    return app.test_client()
