from datetime import date

import pytest
import xlwt
from sqlalchemy import func, select

from customer_ledger.extensions import db
from customer_ledger.legacy_import_service import (
    LegacyImportError,
    confirm_legacy_import,
    dry_run_legacy_import,
)
from customer_ledger.models import Customer, ImportRecord, Payment, PaymentAllocation, Shipment

HEADERS = [
    "日期",
    "总货款",
    "运费",
    "卸车费",
    "退板吨位",
    "退板金额",
    "问题扣费",
    "平方数",
    "应收款",
    "实收款",
    "欠款",
    "抹零",
    "付款方式",
]


def _write_legacy(
    path, first_payment=1200.0, first_old_receivable=1000.0, first_old_balance=-200.0
):
    workbook = xlwt.Workbook()
    sheet = workbook.add_sheet("合成旧账客户")
    date_style = xlwt.easyxf(num_format_str="yyyy-mm-dd")
    sheet.write(0, 0, "合成旧账客户")
    for column, header in enumerate(HEADERS):
        sheet.write(1, column, header)
    sheet.write(2, 0, date(2026, 5, 1), date_style)
    sheet.write(2, 1, 1000.0)
    sheet.write(2, 8, first_old_receivable)
    sheet.write(2, 9, first_payment)
    sheet.write(2, 10, first_old_balance)
    sheet.write(2, 12, "现金 合成收款")
    sheet.write(3, 0, "合计")
    sheet.write(3, 1, 1000.0)
    sheet.write(4, 0, date(2026, 5, 2), date_style)
    sheet.write(4, 1, 200.0)
    sheet.write(4, 8, 200.0)
    sheet.write(4, 9, 200.0)
    sheet.write(4, 12, "银行转账 合成后续")
    sheet.write(4, 13, "合成备注列")
    workbook.save(str(path))


def _write_legacy_with_missing_payment_method(path):
    workbook = xlwt.Workbook()
    sheet = workbook.add_sheet("待确认付款客户")
    date_style = xlwt.easyxf(num_format_str="yyyy-mm-dd")
    sheet.write(0, 0, "待确认付款客户")
    for column, header in enumerate(HEADERS):
        sheet.write(1, column, header)
    sheet.write(2, 0, date(2026, 5, 4), date_style)
    sheet.write(2, 1, 1000.0)
    sheet.write(2, 8, 1000.0)
    sheet.write(2, 9, 1200.0)
    sheet.write(2, 10, -200.0)
    workbook.save(str(path))


def _write_legacy_with_pending_prepayment(path):
    workbook = xlwt.Workbook()
    sheet = workbook.add_sheet("合成预收客户")
    date_style = xlwt.easyxf(num_format_str="yyyy-mm-dd")
    sheet.write(0, 0, "合成预收客户")
    for column, header in enumerate(HEADERS):
        sheet.write(1, column, header)
    sheet.write(2, 0, date(2026, 5, 3), date_style)
    sheet.write(2, 9, 50.0)
    sheet.write(2, 12, "预收待确认")
    workbook.save(str(path))


def _counts():
    return {
        "customers": db.session.scalar(select(func.count(Customer.id))),
        "shipments": db.session.scalar(select(func.count(Shipment.id))),
        "payments": db.session.scalar(select(func.count(Payment.id))),
        "allocations": db.session.scalar(select(func.count(PaymentAllocation.id))),
        "records": db.session.scalar(select(func.count(ImportRecord.id))),
    }


def test_dry_run_is_read_only_and_confirm_is_idempotent(tmp_path, app):
    source = tmp_path / "synthetic-legacy.xls"
    _write_legacy(source)
    with app.app_context():
        before = _counts()
        dry_run = dry_run_legacy_import(db.session, source, report_directory=tmp_path / "reports")
        assert _counts() == before
        assert dry_run.plan.source_hash
        assert dry_run.candidate_counts["shipment_rows"] == 2
        assert dry_run.candidate_counts["payment_rows"] == 2
        assert dry_run.plan.row_counts["total"] == 1
        assert dry_run.plan.anomaly_counts["non_formal_column_content"] == 1
        assert dry_run.plan.reconciliation_counts == {}
        assert dry_run.report_path.is_file()
        assert len(dry_run.plan.mappings) == 1

        backup = tmp_path / "backup.db"
        imported = confirm_legacy_import(
            db.session,
            dry_run,
            backup,
            confirmed_mappings={"合成旧账客户": "导入后的合成客户"},
        )
        assert backup.is_file()
        assert imported.created_shipments == 2
        assert imported.created_payments == 2
        assert imported.created_allocations == 2
        assert imported.reconciliation["unallocated_cents"] == 20000
        assert imported.reconciliation["net_balance_cents"] == -20000

        repeated = confirm_legacy_import(
            db.session,
            dry_run,
            tmp_path / "backup-repeat.db",
            confirmed_mappings={"合成旧账客户": "导入后的合成客户"},
        )
        assert repeated.created_shipments == 0
        assert repeated.created_payments == 0
        assert repeated.skipped_existing == 2
        assert _counts() == {
            "customers": 1,
            "shipments": 2,
            "payments": 2,
            "allocations": 2,
            "records": 2,
        }


def test_changed_source_hash_is_not_deduplicated_by_date_or_amount(tmp_path, app):
    source = tmp_path / "same-source-name.xls"
    _write_legacy(source, first_payment=1200.0)
    with app.app_context():
        first = dry_run_legacy_import(db.session, source, report_directory=tmp_path / "reports")
        confirm_legacy_import(
            db.session,
            first,
            tmp_path / "first.db",
            confirmed_mappings={"合成旧账客户": "哈希合成客户"},
        )
        _write_legacy(source, first_payment=1300.0)
        changed = dry_run_legacy_import(db.session, source, report_directory=tmp_path / "reports")
        assert changed.plan.source_hash != first.plan.source_hash
        result = confirm_legacy_import(
            db.session,
            changed,
            tmp_path / "changed.db",
            confirmed_mappings={"合成旧账客户": "哈希合成客户"},
        )
        assert result.created_shipments == 2
        assert db.session.scalar(select(func.count(ImportRecord.id))) == 4


def test_backup_failure_writes_nothing(tmp_path, app):
    source = tmp_path / "backup-failure.xls"
    _write_legacy(source)
    with app.app_context():
        dry_run = dry_run_legacy_import(db.session, source, report_directory=tmp_path / "reports")

        def fail_backup(_bind, _path):
            raise LegacyImportError("模拟备份失败")

        with pytest.raises(LegacyImportError, match="模拟备份失败"):
            confirm_legacy_import(
                db.session,
                dry_run,
                tmp_path / "should-not-exist.db",
                confirmed_mappings={"合成旧账客户": "备份失败客户"},
                backup_fn=fail_backup,
            )
        assert _counts() == {
            "customers": 0,
            "shipments": 0,
            "payments": 0,
            "allocations": 0,
            "records": 0,
        }


def test_positive_payment_without_method_blocks_before_any_business_write(tmp_path, app):
    source = tmp_path / "missing-payment-method.xls"
    _write_legacy_with_missing_payment_method(source)
    with app.app_context():
        dry_run = dry_run_legacy_import(db.session, source, report_directory=tmp_path / "reports")
        assert dry_run.candidate_counts["payment_rows"] == 1
        assert dry_run.candidate_counts["payment_method_pending_rows"] == 1
        before = _counts()
        with pytest.raises(LegacyImportError, match="付款方式"):
            confirm_legacy_import(
                db.session,
                dry_run,
                tmp_path / "must-not-be-created.db",
                confirmed_mappings={"待确认付款客户": "付款方式待确认客户"},
            )
        assert _counts() == before == {
            "customers": 0,
            "shipments": 0,
            "payments": 0,
            "allocations": 0,
            "records": 0,
        }
        assert not (tmp_path / "must-not-be-created.db").exists()


def test_confirmed_payment_method_imports_full_payment_and_is_idempotent(tmp_path, app):
    source = tmp_path / "confirmed-payment-method.xls"
    _write_legacy_with_missing_payment_method(source)
    with app.app_context():
        dry_run = dry_run_legacy_import(db.session, source, report_directory=tmp_path / "reports")
        row = dry_run.plan.rows[0]
        imported = confirm_legacy_import(
            db.session,
            dry_run,
            tmp_path / "confirmed.db",
            confirmed_mappings={"待确认付款客户": "确认付款客户"},
            confirmed_payment_methods={row.source_key: "现金"},
        )
        assert imported.created_shipments == 1
        assert imported.created_payments == 1
        assert imported.created_allocations == 1
        assert db.session.scalar(select(Payment.amount_cents)) == 120_000
        assert db.session.scalar(select(PaymentAllocation.allocated_amount_cents)) == 100_000
        repeated = confirm_legacy_import(
            db.session,
            dry_run,
            tmp_path / "confirmed-repeat.db",
            confirmed_mappings={"待确认付款客户": "确认付款客户"},
            confirmed_payment_methods={row.source_key: "现金"},
        )
        assert repeated.created_shipments == 0
        assert repeated.created_payments == 0
        assert repeated.created_allocations == 0
        assert repeated.created_import_records == 0
        assert repeated.skipped_existing == 1
        assert _counts() == {
            "customers": 1,
            "shipments": 1,
            "payments": 1,
            "allocations": 1,
            "records": 1,
        }


def test_failed_payment_method_confirmation_can_be_corrected_without_import_record(
    tmp_path, app
):
    source = tmp_path / "retry-payment-method.xls"
    _write_legacy_with_missing_payment_method(source)
    with app.app_context():
        dry_run = dry_run_legacy_import(db.session, source, report_directory=tmp_path / "reports")
        row = dry_run.plan.rows[0]
        with pytest.raises(LegacyImportError, match="付款方式"):
            confirm_legacy_import(
                db.session,
                dry_run,
                tmp_path / "retry-failed.db",
                confirmed_mappings={"待确认付款客户": "可重试客户"},
            )
        assert _counts() == {
            "customers": 0,
            "shipments": 0,
            "payments": 0,
            "allocations": 0,
            "records": 0,
        }
        imported = confirm_legacy_import(
            db.session,
            dry_run,
            tmp_path / "retry-success.db",
            confirmed_mappings={"待确认付款客户": "可重试客户"},
            confirmed_payment_methods={row.source_key: "现金"},
        )
        assert imported.created_import_records == 1
        assert _counts()["records"] == 1


def test_legacy_import_page_shows_pending_payment_method_and_passes_choice(
    client, app, tmp_path
):
    source = tmp_path / "page-payment-method.xls"
    _write_legacy_with_missing_payment_method(source)
    app.config["IMPORT_REPORT_DIR"] = str(tmp_path / "reports")
    preview = client.post(
        "/imports/legacy",
        data={"source_path": str(source), "action": "dry_run"},
    )
    preview_body = preview.get_data(as_text=True)
    assert preview.status_code == 200
    assert "付款方式待确认" in preview_body
    assert "待确认付款客户" in preview_body
    assert "原始行号" in preview_body
    with app.app_context():
        dry_run = dry_run_legacy_import(db.session, source, report_directory=tmp_path / "reports")
        row = dry_run.plan.rows[0]
        response = client.post(
            "/imports/legacy",
            data={
                "source_path": str(source),
                "action": "confirm",
                "mapping_0": "网页确认客户",
                f"payment_method_{row.source_key}": "现金",
                "backup_path": str(tmp_path / "page-confirmed.db"),
            },
        )
        assert response.status_code == 200
        assert "导入完成" in response.get_data(as_text=True)
        assert _counts() == {
            "customers": 1,
            "shipments": 1,
            "payments": 1,
            "allocations": 1,
            "records": 1,
        }


def test_legacy_import_page_without_payment_method_writes_nothing(client, app, tmp_path):
    source = tmp_path / "page-missing-payment-method.xls"
    _write_legacy_with_missing_payment_method(source)
    app.config["IMPORT_REPORT_DIR"] = str(tmp_path / "reports")
    response = client.post(
        "/imports/legacy",
        data={
            "source_path": str(source),
            "action": "confirm",
            "mapping_0": "网页阻塞客户",
            "backup_path": str(tmp_path / "page-must-not-be-created.db"),
        },
    )
    assert response.status_code == 200
    assert "需要明确付款方式" in response.get_data(as_text=True)
    with app.app_context():
        assert _counts() == {
            "customers": 0,
            "shipments": 0,
            "payments": 0,
            "allocations": 0,
            "records": 0,
        }
    assert not (tmp_path / "page-must-not-be-created.db").exists()


def test_confirmed_prepayment_without_method_blocks_then_imports_after_confirmation(tmp_path, app):
    source = tmp_path / "prepayment-method-confirmation.xls"
    _write_legacy_with_pending_prepayment(source)
    with app.app_context():
        dry_run = dry_run_legacy_import(db.session, source, report_directory=tmp_path / "reports")
        row = dry_run.plan.rows[0]
        with pytest.raises(LegacyImportError, match="付款方式"):
            confirm_legacy_import(
                db.session,
                dry_run,
                tmp_path / "prepayment-failed.db",
                confirmed_mappings={"合成预收客户": "预收确认客户"},
                confirm_prepayments=True,
            )
        assert _counts() == {
            "customers": 0,
            "shipments": 0,
            "payments": 0,
            "allocations": 0,
            "records": 0,
        }
        imported = confirm_legacy_import(
            db.session,
            dry_run,
            tmp_path / "prepayment-success.db",
            confirmed_mappings={"合成预收客户": "预收确认客户"},
            confirm_prepayments=True,
            confirmed_payment_methods={row.source_key: "现金"},
        )
        assert imported.created_payments == 1
        assert imported.created_import_records == 1


def test_reconciliation_difference_is_warning_only_and_current_formula_wins(tmp_path, app):
    source = tmp_path / "reconciliation-warning.xls"
    _write_legacy(source, first_old_receivable=999.0)
    with app.app_context():
        dry_run = dry_run_legacy_import(db.session, source, report_directory=tmp_path / "reports")
        assert dry_run.plan.reconciliation_counts["receivable_difference_rows"] == 1
        imported = confirm_legacy_import(
            db.session,
            dry_run,
            tmp_path / "reconciliation-warning.db",
            confirmed_mappings={"合成旧账客户": "对账提示客户"},
        )
        assert imported.reconciliation["receivable_cents"] == 120_000
        assert imported.reconciliation["allocated_cents"] == 120_000
def test_unconfirmed_prepayment_is_pending_and_not_written(tmp_path, app):
    source = tmp_path / "pending-prepayment.xls"
    _write_legacy_with_pending_prepayment(source)
    with app.app_context():
        dry_run = dry_run_legacy_import(db.session, source, report_directory=tmp_path / "reports")
        assert dry_run.candidate_counts["pending_prepayment_rows"] == 1
        result = confirm_legacy_import(
            db.session,
            dry_run,
            tmp_path / "pending-backup.db",
            confirmed_mappings={"合成预收客户": "待确认预收客户"},
        )
        assert result.pending_prepayments == 1
        assert _counts() == {
            "customers": 0,
            "shipments": 0,
            "payments": 0,
            "allocations": 0,
            "records": 0,
        }
        prepayment_row = dry_run.plan.rows[0]
        confirmed = confirm_legacy_import(
            db.session,
            dry_run,
            tmp_path / "confirmed-prepayment-backup.db",
            confirmed_mappings={"合成预收客户": "已确认预收客户"},
            confirm_prepayments=True,
            confirmed_payment_methods={prepayment_row.source_key: "现金"},
        )
        assert confirmed.created_payments == 1
        assert confirmed.created_allocations == 0
        assert confirmed.reconciliation["unallocated_cents"] == 5000
        assert _counts() == {
            "customers": 1,
            "shipments": 0,
            "payments": 1,
            "allocations": 0,
            "records": 1,
        }


def test_import_rolls_back_everything_when_a_later_row_fails(tmp_path, app):
    source = tmp_path / "rollback.xls"
    _write_legacy(source)
    with app.app_context():
        dry_run = dry_run_legacy_import(db.session, source, report_directory=tmp_path / "reports")
        second_row = dry_run.plan.rows[1]
        with pytest.raises(ValueError):
            confirm_legacy_import(
                db.session,
                dry_run,
                tmp_path / "rollback-backup.db",
                confirmed_mappings={"合成旧账客户": "回滚合成客户"},
                confirmed_payment_methods={second_row.source_key: "不是付款方式"},
            )
        assert _counts() == {
            "customers": 0,
            "shipments": 0,
            "payments": 0,
            "allocations": 0,
            "records": 0,
        }
