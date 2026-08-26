from __future__ import annotations

import json
from datetime import date
from io import BytesIO
from pathlib import Path

import pytest
from openpyxl import load_workbook
from sqlalchemy import func, select

from customer_ledger.backup_service import (
    BackupError,
    create_backup,
    list_backups,
    prune_backups,
    restore_database,
    validate_backup,
)
from customer_ledger.bookkeeping_service import (
    ShipmentInput,
    create_shipment_with_initial_payment,
)
from customer_ledger.calculation_service import customer_summary, verify_accounting_identities
from customer_ledger.customer_service import create_customer
from customer_ledger.extensions import db
from customer_ledger.models import Customer, Payment, Shipment


def _customer(name: str) -> Customer:
    customer = create_customer(db.session, name)
    db.session.commit()
    return customer


def _shipment_input(customer_id: int, *, total_amount_cents: int = 100_000) -> ShipmentInput:
    return ShipmentInput(
        customer_id=customer_id,
        shipment_date=date(2026, 8, 1),
        total_amount_cents=total_amount_cents,
        freight_cents=0,
        unloading_fee_cents=0,
        returned_pallet_tonnage_hundredths=0,
        returned_pallet_amount_cents=0,
        issue_deduction_cents=0,
        area_hundredths=0,
        rounding_cents=0,
        description="阶段四合成记录",
    )


def test_ledger_footer_uses_allocations_and_customer_summary_keeps_prepayment(client, app):
    with app.app_context():
        customer = _customer("阶段四合成预收客户")
        create_shipment_with_initial_payment(
            db.session,
            _shipment_input(customer.id),
            120_000,
            "现金",
            "合成收款",
            "stage4-footer-token",
        )
        summary = customer_summary(db.session, customer.id)
        assert summary.total_received_cents == 120_000
        assert summary.total_allocated_received_cents == 100_000
        assert summary.unallocated_prepayment_cents == 20_000
        assert summary.net_balance_cents == -20_000
        body = client.get(f"/customers/{customer.id}/ledger").get_data(as_text=True)
        footer = body.split("<tfoot>", 1)[1].split("</tfoot>", 1)[0]
        assert "1000.00" in footer
        assert "1200.00" not in footer
        assert "预收余额" in body
        assert "欠款 -200.00" not in body
        verify_accounting_identities(db.session)


def test_negative_and_unpaid_ui_are_understandable_and_do_not_create_zero_payment(client, app):
    with app.app_context():
        customer = _customer("阶段四合成负数客户")
        response = client.get("/shipments/new")
        body = response.get_data(as_text=True)
        assert response.status_code == 200
        assert "暂未付款" in body
        assert "冲减" in body
        assert "欠款 ${" in body

        response = client.post(
            "/shipments/new",
            data={
                "submission_token": "stage4-unpaid-token",
                "customer_id": str(customer.id),
                "shipment_date": "2026-08-02",
                "total_amount": "1000.00",
                "freight": "0",
                "unloading_fee": "0",
                "returned_pallet_tonnage": "0",
                "returned_pallet_amount": "0",
                "issue_deduction": "0",
                "area": "0",
                "rounding": "0",
                "description": "",
                "initial_received": "",
                "payment_method": "暂未付款",
                "payment_description": "",
            },
        )
        assert response.status_code == 302
        assert db.session.scalar(select(func.count(Payment.id))) == 0
        assert db.session.scalar(select(func.count(Shipment.id))) == 1


def test_backup_manifest_is_private_and_validates_integrity(app, tmp_path):
    with app.app_context():
        _customer("阶段四合成备份客户")
        backup_dir = tmp_path / "backup-files"
        manifest = create_backup(db.engine, backup_dir=backup_dir, reason="manual")
        manifest_data = json.loads(
            (backup_dir / manifest.manifest_filename).read_text(encoding="utf-8")
        )
        assert set(manifest_data) == {
            "format_version",
            "database_filename",
            "created_at",
            "reason",
            "app_version",
            "schema_version",
            "file_size_bytes",
            "sha256",
        }
        validate_backup(backup_dir / manifest.manifest_filename)
        (backup_dir / manifest.database_filename).open("ab").write(b"corruption")
        with pytest.raises(BackupError, match="校验失败|大小"):
            validate_backup(backup_dir / manifest.manifest_filename)


def test_restore_replaces_atomically_and_keeps_pre_restore_backup(app, tmp_path):
    with app.app_context():
        first = _customer("阶段四恢复前客户")
        backup_dir = tmp_path / "backup-files"
        manifest = create_backup(db.engine, backup_dir=backup_dir, reason="manual")
        _customer("阶段四恢复后客户")
        restore = restore_database(
            db.engine,
            backup_dir / manifest.manifest_filename,
            backup_dir=backup_dir,
            post_restore_check=lambda: verify_accounting_identities(db.session),
        )
        db.session.remove()
        assert db.session.get(Customer, first.id) is not None
        assert (
            db.session.scalar(select(Customer).where(Customer.name == "阶段四恢复后客户")) is None
        )
        assert restore.pre_restore.reason == "before_restore"
        assert any(item.reason == "before_restore" for item in list_backups(backup_dir))


def test_failed_post_restore_check_rolls_back_to_current_database(app, tmp_path):
    with app.app_context():
        _customer("阶段四失败回滚基线")
        backup_dir = tmp_path / "backup-files"
        manifest = create_backup(db.engine, backup_dir=backup_dir, reason="manual")
        changed = _customer("阶段四失败回滚保留")
        changed_id = changed.id

        def fail_check():
            raise ValueError("合成校验失败")

        with pytest.raises(BackupError, match="自动回滚"):
            restore_database(
                db.engine,
                backup_dir / manifest.manifest_filename,
                backup_dir=backup_dir,
                post_restore_check=fail_check,
            )
        db.session.remove()
        assert db.session.get(Customer, changed_id) is not None


def test_export_creates_before_export_backup_and_audit_page_hides_snapshots(client, app):
    with app.app_context():
        _customer("阶段四导出审计客户")
        response = client.get("/exports/summary.xlsx")
        assert response.status_code == 200
        reasons = [item.reason for item in list_backups(app.config["BACKUP_DIR"])]
        assert "before_export" in reasons
        audit_body = client.get("/audit").get_data(as_text=True)
        assert "操作记录" in audit_body
        assert "导出前备份" in audit_body
        assert "amount_cents" not in audit_body
        assert "内部说明" not in audit_body


def test_manual_backup_page_requires_second_confirmation_and_restore_route(client, app):
    with app.app_context():
        _customer("阶段四页面恢复客户")
        assert client.post("/backups/create").status_code == 302
        backup = next(
            item for item in list_backups(app.config["BACKUP_DIR"]) if item.reason == "manual"
        )
        confirm = client.get(f"/backups/{backup.manifest_filename}/restore")
        assert confirm.status_code == 200
        assert "当前账目将恢复到上述备份状态" in confirm.get_data(as_text=True)
        blocked = client.post(f"/backups/{backup.manifest_filename}/restore", data={})
        assert blocked.status_code == 400
        assert "二次确认" in blocked.get_data(as_text=True)


def test_ledger_pagination_is_stable(client, app):
    app.config["LEDGER_PAGE_SIZE"] = 2
    with app.app_context():
        customer = _customer("阶段四分页客户")
        for index in range(3):
            create_shipment_with_initial_payment(
                db.session,
                _shipment_input(customer.id),
                0,
                "现金",
                "",
                f"stage4-page-token-{index}",
            )
        body = client.get(f"/customers/{customer.id}/ledger?shipment_page=2").get_data(as_text=True)
        assert "当前显示第 2 / 2 页" in body
        assert "下一页" not in body
        assert "上一页" in body


def test_templates_are_local_and_stage4_user_text_is_chinese():
    template_dir = Path(__file__).parents[1] / "src" / "customer_ledger" / "templates"
    content = "\n".join(path.read_text(encoding="utf-8") for path in template_dir.glob("*.html"))
    for text in ("Customer ledger", "Bookkeeping", "Read-only report", "https://", "http://"):
        assert text not in content


def test_retention_only_removes_old_valid_application_backups(app, tmp_path):
    with app.app_context():
        _customer("阶段四保留策略客户")
        backup_dir = tmp_path / "backup-files"
        for _ in range(31):
            create_backup(db.engine, backup_dir=backup_dir, reason="manual")
        unknown = backup_dir / "do-not-touch.txt"
        unknown.write_text("synthetic", encoding="utf-8")
        assert len([item for item in list_backups(backup_dir) if item.valid]) <= 30
        assert unknown.read_text(encoding="utf-8") == "synthetic"
        assert prune_backups(backup_dir) == []


def test_customer_ledger_page_does_not_expose_absolute_paths(client, app):
    with app.app_context():
        customer = _customer("阶段四错误提示客户")
        body = client.get(f"/customers/{customer.id}/ledger").get_data(as_text=True)
        assert str(Path(app.config["BACKUP_DIR"]).resolve()) not in body
        assert "SQLAlchemy" not in body
        assert "SQLite" not in body


def test_daily_startup_backup_is_once_per_local_date(client, app):
    with app.app_context():
        _customer("阶段四每日备份客户")
        client.get("/")
        first = [
            item
            for item in list_backups(app.config["BACKUP_DIR"])
            if item.reason == "daily_startup"
        ]
        app.extensions["daily_backup_date"] = None
        client.get("/healthz")
        second = [
            item
            for item in list_backups(app.config["BACKUP_DIR"])
            if item.reason == "daily_startup"
        ]
        assert len(first) == len(second) == 1


def test_full_synthetic_stage4_flow_reads_all_workbooks_and_restores(client, app):
    with app.app_context():
        customer = _customer("阶段四端到端客户")
        customer_id = customer.id
        shipment = create_shipment_with_initial_payment(
            db.session,
            _shipment_input(customer.id),
            100_000,
            "银行转账",
            "",
            "stage4-e2e-shipment",
        )
        create_shipment_with_initial_payment(
            db.session,
            _shipment_input(customer.id),
            0,
            "现金",
            "",
            "stage4-e2e-second-shipment",
        )
        payment = client.post(
            "/payments/new",
            data={
                "submission_token": "stage4-e2e-payment",
                "customer_id": str(customer.id),
                "payment_date": "2026-08-03",
                "amount": "600.00",
                "payment_method": "微信",
                "description": "",
                "allocation_mode": "none",
            },
        )
        assert payment.status_code == 302
        payment_record = db.session.scalar(
            select(Payment).where(Payment.customer_id == customer.id).order_by(Payment.id.desc())
        )
        allocation = client.post(
            f"/payments/{payment_record.id}/allocate",
            data={
                "submission_token": "stage4-e2e-allocation",
                "shipment_id": str(shipment.id),
                "allocation_amount": "600.00",
            },
        )
        assert allocation.status_code == 302
        for path in (
            "/customers/1/export.xlsx",
            "/exports/summary.xlsx",
            "/exports/all-ledgers.xlsx",
        ):
            response = client.get(path.replace("/customers/1", f"/customers/{customer.id}"))
            assert response.status_code == 200
            workbook = load_workbook(BytesIO(response.data), data_only=True)
            assert workbook.worksheets
        client.post("/backups/create")
        manual = next(
            item for item in list_backups(app.config["BACKUP_DIR"]) if item.reason == "manual"
        )
        later = _customer("阶段四端到端待恢复客户")
        later_id = later.id
        db.session.commit()
        confirmation = client.get(
            f"/backups/{manual.manifest_filename}/restore"
        )
        assert confirmation.status_code == 200
        restored = client.post(
            f"/backups/{manual.manifest_filename}/restore",
            data={"confirm_restore": "yes"},
        )
        assert restored.status_code == 302
        db.session.remove()
        assert db.session.get(Customer, later_id) is None
        assert client.get(f"/customers/{customer_id}/ledger").status_code == 200
        assert "完成恢复" in client.get("/audit").get_data(as_text=True)
