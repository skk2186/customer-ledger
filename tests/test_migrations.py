import pytest
from flask_migrate import upgrade
from sqlalchemy import inspect

from customer_ledger import create_app, db
from customer_ledger.models import Customer


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
        "submission_record",
    } <= tables


def test_existing_0002_database_upgrades_to_31_without_data_loss(tmp_path):
    database_path = tmp_path / "legacy.db"
    legacy_app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{database_path}",
        }
    )
    with legacy_app.app_context():
        upgrade(revision="0002_unique_normalized_name_index")
        db.session.add(Customer(name="旧库客户", normalized_name="旧库客户"))
        db.session.commit()
        upgrade()
        db.session.expire_all()
        customer = db.session.get(Customer, 1)
        name_column = next(
            column
            for column in inspect(db.engine).get_columns("customer")
            if column["name"] == "name"
        )
        assert customer.name == "旧库客户"
        assert name_column["type"].length == 31
        db.session.remove()
        db.engine.dispose()


def test_existing_overlong_name_blocks_migration_without_truncation(tmp_path):
    database_path = tmp_path / "overlong.db"
    legacy_app = create_app(
        {
            "TESTING": True,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{database_path}",
        }
    )
    with legacy_app.app_context():
        upgrade(revision="0002_unique_normalized_name_index")
        overlong_name = "旧" * 32
        db.session.add(Customer(name=overlong_name, normalized_name=overlong_name))
        db.session.commit()
        with pytest.raises(SystemExit) as migration_error:
            upgrade()
        assert migration_error.value.code == 1
        stored_name = db.session.scalar(db.select(Customer.name).where(Customer.id == 1))
        assert stored_name == overlong_name
        db.session.remove()
        db.engine.dispose()
