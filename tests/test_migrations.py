from flask_migrate import upgrade
from sqlalchemy import inspect


def test_empty_database_migrates_and_repeat_is_safe(app):
    with app.app_context():
        upgrade()
        upgrade()
        tables = set(inspect(app.extensions["sqlalchemy"].engine).get_table_names())

    assert {
        "alembic_version",
        "customer",
        "shipment",
        "payment",
        "payment_allocation",
        "audit_event",
        "import_record",
    } <= tables
