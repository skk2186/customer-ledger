from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from customer_ledger import backup_service as backup_module
from customer_ledger import create_app, db
from customer_ledger import routes as routes_module
from customer_ledger.backup_service import (
    BackupError,
    create_backup,
    list_backups,
    restore_database,
)
from customer_ledger.bookkeeping_service import (
    PaymentInput,
    ShipmentInput,
    create_payment_workflow,
    create_shipment_with_initial_payment,
)
from customer_ledger.models import AuditEvent, Customer, Payment, PaymentAllocation, Shipment


def _customer(name: str) -> Customer:
    customer = Customer(name=name, normalized_name=name.casefold())
    db.session.add(customer)
    db.session.commit()
    return customer


def _shipment_input(customer_id: int, *, day: int = 1) -> ShipmentInput:
    return ShipmentInput(
        customer_id=customer_id,
        shipment_date=date(2026, 8, day),
        total_amount_cents=100_000,
        freight_cents=0,
        unloading_fee_cents=0,
        returned_pallet_tonnage_hundredths=0,
        returned_pallet_amount_cents=0,
        issue_deduction_cents=0,
        area_hundredths=0,
        rounding_cents=0,
        description="合成恢复安全测试",
    )


def _make_manual_backup(app, name: str):
    customer = _customer(name)
    manifest = create_backup(
        db.engine,
        backup_dir=app.config["BACKUP_DIR"],
        reason="manual",
        prune=False,
    )
    return customer, manifest


def test_before_restore_backup_failure_keeps_database_and_reports_chinese(
    client, app, monkeypatch
):
    with app.app_context():
        original, manifest = _make_manual_backup(app, "合成恢复前备份失败")
        original_id = original.id
        changed = _customer("合成恢复前备份失败后仍在")
        changed_id = changed.id

        original_create_backup = backup_module.create_backup

        def fail_before_restore(bind, **kwargs):
            if kwargs.get("reason") == "before_restore":
                raise BackupError("合成恢复前备份失败")
            return original_create_backup(bind, **kwargs)

        monkeypatch.setattr(backup_module, "create_backup", fail_before_restore)
        response = client.post(
            f"/backups/{manifest.manifest_filename}/restore",
            data={"confirm_restore": "yes"},
        )

        assert response.status_code == 503
        assert "恢复未完成" in response.get_data(as_text=True)
        db.session.remove()
        assert db.session.get(Customer, original_id) is not None
        assert db.session.get(Customer, changed_id) is not None
        assert not Path(app.config["SAFETY_LOCK_PATH"]).exists()


@pytest.mark.parametrize("failure_kind", ["hash", "integrity", "schema"])
def test_invalid_restore_backup_never_replaces_database(app, failure_kind):
    with app.app_context():
        original, manifest = _make_manual_backup(app, f"合成恢复校验{failure_kind}")
        original_id = original.id
        changed = _customer(f"合成恢复校验{failure_kind}后仍在")
        changed_id = changed.id
        manifest_path = Path(app.config["BACKUP_DIR"]) / manifest.manifest_filename
        database_path = Path(app.config["BACKUP_DIR"]) / manifest.database_filename
        manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))

        if failure_kind == "hash":
            database_path.open("ab").write(b"synthetic-corruption")
        elif failure_kind == "integrity":
            broken = b"not a sqlite database"
            database_path.write_bytes(broken)
            manifest_data["file_size_bytes"] = len(broken)
            manifest_data["sha256"] = hashlib.sha256(broken).hexdigest()
            manifest_path.write_text(
                json.dumps(manifest_data, ensure_ascii=False), encoding="utf-8"
            )
        else:
            manifest_data["schema_version"] = "incompatible-synthetic-schema"
            manifest_path.write_text(
                json.dumps(manifest_data, ensure_ascii=False), encoding="utf-8"
            )

        with pytest.raises(BackupError):
            restore_database(
                db.engine,
                manifest_path,
                backup_dir=app.config["BACKUP_DIR"],
                safety_lock_path=app.config["SAFETY_LOCK_PATH"],
            )
        db.session.remove()
        assert db.session.get(Customer, original_id) is not None
        assert db.session.get(Customer, changed_id) is not None
        assert not Path(app.config["SAFETY_LOCK_PATH"]).exists()


def test_atomic_replace_failure_keeps_old_database_queryable(app, monkeypatch):
    with app.app_context():
        original, manifest = _make_manual_backup(app, "合成原子替换失败")
        original_id = original.id
        changed = _customer("合成原子替换失败后仍在")
        changed_id = changed.id
        live_database = Path(db.engine.url.database).resolve()
        original_replace = backup_module.os.replace

        def fail_first_restore_replace(source, destination):
            if (
                Path(destination).resolve() == live_database
                and Path(source).suffix == ".restore"
            ):
                raise OSError("合成原子替换失败")
            return original_replace(source, destination)

        monkeypatch.setattr(backup_module.os, "replace", fail_first_restore_replace)
        db.session.remove()
        db.engine.dispose()
        with pytest.raises(BackupError, match="自动回滚"):
            restore_database(
                db.engine,
                Path(app.config["BACKUP_DIR"]) / manifest.manifest_filename,
                backup_dir=app.config["BACKUP_DIR"],
                safety_lock_path=app.config["SAFETY_LOCK_PATH"],
            )
        db.session.remove()
        assert db.session.get(Customer, original_id) is not None
        assert db.session.get(Customer, changed_id) is not None
        assert not Path(app.config["SAFETY_LOCK_PATH"]).exists()


def test_restore_audit_failure_rolls_back_and_reports_failure(client, app, monkeypatch):
    with app.app_context():
        original, manifest = _make_manual_backup(app, "合成恢复审计失败")
        original_id = original.id
        changed = _customer("合成恢复审计失败后仍在")
        changed_id = changed.id
        before_completed = db.session.scalar(
            select(func.count(AuditEvent.id)).where(
                AuditEvent.object_type == "restore",
                AuditEvent.action == "completed",
            )
        )
        original_audit = routes_module.add_system_audit

        def fail_restore_audit(session, object_type, action, **kwargs):
            if object_type == "restore" and action == "completed":
                raise SQLAlchemyError("合成审计提交失败")
            return original_audit(session, object_type, action, **kwargs)

        monkeypatch.setattr(routes_module, "add_system_audit", fail_restore_audit)
        response = client.post(
            f"/backups/{manifest.manifest_filename}/restore",
            data={"confirm_restore": "yes"},
        )

        assert response.status_code == 503
        assert "恢复未完成" in response.get_data(as_text=True)
        db.session.remove()
        assert db.session.get(Customer, original_id) is not None
        assert db.session.get(Customer, changed_id) is not None
        after_completed = db.session.scalar(
            select(func.count(AuditEvent.id)).where(
                AuditEvent.object_type == "restore",
                AuditEvent.action == "completed",
            )
        )
        assert after_completed == before_completed
        assert not Path(app.config["SAFETY_LOCK_PATH"]).exists()


def test_rollback_failure_creates_persistent_lock_and_blocks_writes_after_restart(
    client, app, monkeypatch
):
    with app.app_context():
        _original, manifest = _make_manual_backup(app, "合成回滚失败基线")
        _customer("合成回滚失败保留")
        counts_before_lock = {
            "customers": db.session.scalar(select(func.count(Customer.id))),
            "shipments": db.session.scalar(select(func.count(Shipment.id))),
            "payments": db.session.scalar(select(func.count(Payment.id))),
        }
        live_database = Path(db.engine.url.database).resolve()
        original_replace = backup_module.os.replace

        def fail_rollback_replace(source, destination):
            if (
                Path(destination).resolve() == live_database
                and Path(source).suffix == ".rollback"
            ):
                raise OSError("合成回滚替换失败")
            return original_replace(source, destination)

        def fail_post_restore_check(_session):
            raise ValueError("合成恢复后校验失败")

        monkeypatch.setattr(backup_module.os, "replace", fail_rollback_replace)
        monkeypatch.setattr(routes_module, "verify_accounting_identities", fail_post_restore_check)
        response = client.post(
            f"/backups/{manifest.manifest_filename}/restore",
            data={"confirm_restore": "yes"},
        )

        assert response.status_code == 503
        assert "保护状态" in response.get_data(as_text=True)
        lock_path = Path(app.config["SAFETY_LOCK_PATH"])
        assert lock_path.is_file()
        lock_data = json.loads(lock_path.read_text(encoding="utf-8"))
        assert set(lock_data) == {"created_at", "reason_code", "error_category"}
        assert "合成回滚失败保留" not in lock_path.read_text(encoding="utf-8")

        for path in ("/customers/new", "/shipments/new", "/payments/new"):
            assert client.post(path, data={}).status_code == 503
        assert (
            db.session.scalar(select(func.count(Customer.id)))
            == counts_before_lock["customers"] - 1
        )
        assert db.session.scalar(select(func.count(Shipment.id))) == counts_before_lock["shipments"]
        assert db.session.scalar(select(func.count(Payment.id))) == counts_before_lock["payments"]

        db.session.remove()
        db.engine.dispose()
        restarted = create_app(
            {
                "TESTING": True,
                "SQLALCHEMY_DATABASE_URI": f"sqlite:///{live_database}",
                "BACKUP_DIR": app.config["BACKUP_DIR"],
                "SAFETY_LOCK_PATH": app.config["SAFETY_LOCK_PATH"],
            }
        )
        restarted_client = restarted.test_client()
        assert restarted_client.post("/customers/new", data={}).status_code == 503
        assert restarted_client.get("/").status_code == 200
        with restarted.app_context():
            db.session.remove()
            db.engine.dispose()


def test_read_only_pages_remain_available_under_safety_lock(client, app):
    with app.app_context():
        lock_path = Path(app.config["SAFETY_LOCK_PATH"])
        lock_path.write_text(
            json.dumps(
                {
                    "created_at": "2026-08-01T00:00:00+08:00",
                    "reason_code": "restore_rollback_failed",
                    "error_category": "restore",
                }
            ),
            encoding="utf-8",
        )
        for path in ("/", "/backups", "/audit", "/summary"):
            response = client.get(path)
            assert response.status_code == 200
        assert client.get("/exports/summary.xlsx").status_code == 503


def test_daily_backup_runs_once_on_each_injected_local_date(client, app):
    clock = {"value": datetime(2026, 8, 1, 12, tzinfo=timezone.utc)}
    app.config["LOCAL_NOW_PROVIDER"] = lambda: clock["value"]
    with app.app_context():
        client.get("/")
        app.extensions["daily_backup_date"] = None
        clock["value"] = datetime(2026, 8, 2, 12, tzinfo=timezone.utc)
        client.get("/")
        app.extensions["daily_backup_date"] = None
        client.get("/")
        daily = [
            item
            for item in list_backups(app.config["BACKUP_DIR"])
            if item.reason == "daily_startup"
        ]
        assert len(daily) == 2
        assert {item.created_date for item in daily} == {"2026-08-01", "2026-08-02"}


def test_unknown_external_backup_is_not_listed_or_restorable(client, app):
    with app.app_context():
        external_manifest = Path(app.config["BACKUP_DIR"]) / "xxx.json"
        external_manifest.parent.mkdir(parents=True, exist_ok=True)
        external_manifest.write_text(
            json.dumps(
                {
                    "format_version": "1",
                    "database_filename": "xxx.db",
                    "created_at": "2026-08-01T00:00:00+08:00",
                    "reason": "external",
                    "app_version": "unknown",
                    "schema_version": "unknown",
                    "file_size_bytes": 1,
                    "sha256": "0" * 64,
                }
            ),
            encoding="utf-8",
        )
        (external_manifest.with_suffix(".db")).write_bytes(b"x")
        assert all(
            item.manifest_filename != "xxx.json"
            for item in list_backups(app.config["BACKUP_DIR"])
        )
        assert "xxx.json" not in client.get("/backups").get_data(as_text=True)
        assert client.get("/backups/xxx.json/restore").status_code == 404


def test_shipment_and_payment_pagination_are_independent_and_support_cross_page_allocation(
    client, app
):
    app.config["LEDGER_PAGE_SIZE"] = 2
    with app.app_context():
        customer = _customer("合成双分页客户")
        shipments = [
            create_shipment_with_initial_payment(
                db.session,
                _shipment_input(customer.id, day=index + 1),
                0,
                "现金",
                "",
                f"stage4-pagination-shipment-{index}",
            )
            for index in range(5)
        ]
        payments = [
            create_payment_workflow(
                db.session,
                PaymentInput(
                    customer_id=customer.id,
                    payment_date=date(2026, 8, 10 + index),
                    amount_cents=10_000,
                    payment_method="现金",
                    description="合成分页收款",
                ),
                "none",
                [],
                f"stage4-pagination-payment-{index}",
            )
            for index in range(5)
        ]
        body = client.get(
            f"/customers/{customer.id}/ledger?shipment_page=2&payment_page=1"
        ).get_data(as_text=True)
        assert "当前显示第 2 / 3 页" in body
        assert "当前显示第 1 / 3 页" in body
        assert f"#{shipments[2].id}" in body
        allocation = client.post(
            f"/payments/{payments[-1].id}/allocate",
            data={
                "submission_token": "stage4-cross-page-allocation",
                "shipment_id": str(shipments[2].id),
                "allocation_amount": "100.00",
            },
        )
        assert allocation.status_code == 302
        assert db.session.scalar(select(func.count(PaymentAllocation.id))) == 1
        stored = db.session.scalar(select(PaymentAllocation))
        assert stored.shipment_id == shipments[2].id
        body = client.get(
            f"/customers/{customer.id}/ledger?shipment_page=2&payment_page=2"
        ).get_data(as_text=True)
        shipment_section, payment_section = body.split(
            '<section class="section-heading"><h2>收款记录', 1
        )
        assert "当前显示第 2 / 3 页" in shipment_section
        assert "当前显示第 2 / 3 页" in payment_section
        assert f"#{shipments[2].id}" in body
